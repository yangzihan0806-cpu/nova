#!/usr/bin/env python3
"""Contract v1.2 HTTP smoke test; run against a disposable, otherwise idle DB.

Three HTTP clients simulate workers; none imports the native worker. The real
backend/mandelbrot.py supplies computation. No server reset or DB access occurs.
"""

from __future__ import annotations

import argparse
import math
import multiprocessing
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
QUICK = {
    "width": 512, "height": 384, "tile_size": 64, "max_iter": 256,
    "x_min": -2.2, "x_max": 1.0, "y_min": -1.2, "y_max": 1.2,
}
STAGES = [
    ("stage1", "health, presets, disposable DB precondition"),
    ("stage2", "invalid job parameters return 422"),
    ("stage3", "register W1/W2/W3 across two device IDs; invalid tokens return 401"),
    ("stage4", "compute reference tiles; submit quick job (48 tasks)"),
    ("stage5", "W1 completes one tile, then abandons its next lease"),
    ("stage6", "W2/W3 complete quick job and recover W1's expired lease"),
    ("stage7", "W1's stale lease returns 409"),
    ("stage8", "duplicate complete returns 409; exactly 48 tasks counted"),
    ("stage9", "wrong-length and zero-filled results return 422"),
    ("stage_contributions", "two device rows have exactly 48 accepted tiles and reference iteration credit"),
    ("stage10", "pause prevents leases; resume permits a lease"),
    ("stage11", "disconnect immediately releases the held task"),
    ("stage12", "events include expiry, rejection, departure and completion"),
]


class Failure(Exception):
    """Messages are deliberately free of tokens and raw server responses."""


def require(condition, message):
    if not condition:
        raise Failure(message)


def _precompute_child(backend_path, params, pipe):
    """Isolated so a hung kernel cannot keep the smoke process alive."""
    try:
        sys.path.insert(0, backend_path)
        from mandelbrot import compute_tile, split_inputs
        inputs = list(split_inputs(params))
        entries = []
        for tile in inputs:
            start = time.perf_counter()
            result = compute_tile(tile)
            if hasattr(result, "reshape"):
                result = result.reshape(-1).tolist()
            else:
                result = list(result)
            require(len(result) == tile["tile_w"] * tile["tile_h"],
                    "compute_tile returned the wrong number of pixels")
            require(all(type(n) is int and 1 <= n <= params["max_iter"] for n in result),
                    "compute_tile returned invalid pixels")
            runtime_ms = max(0, round((time.perf_counter() - start) * 1000))
            entries.append((tile, result, runtime_ms))
        pipe.send((True, entries))
    except BaseException as exc:
        # Missing teammates' files must never be mistaken for a successful test.
        pipe.send((False, type(exc).__name__))
    finally:
        pipe.close()


class Smoke:
    def __init__(self, args, requests):
        self.args = args
        self.requests = requests
        self.session = requests.Session()
        self.deadline = time.monotonic() + args.timeout
        self.workers = []
        self.disconnected = set()
        self.cache = {}
        self.event_cursor = 0
        self.completed_ids = set()
        self.worker_devices = {}
        self.worker_tiles = {}
        self.device_credit = {}
        self.recovered = False

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "overall deadline exceeded; increase --timeout if needed")
        return remaining

    def request(self, method, path, expected=200, worker=None, body=None, token=None):
        headers = {}
        if worker is not None:
            headers["Authorization"] = "Bearer " + (token if token is not None else worker["token"])
        try:
            response = self.session.request(
                method, self.args.server + path, json=body, headers=headers,
                timeout=min(self.args.request_timeout, self.remaining()),
                allow_redirects=False,
            )
        except self.requests.RequestException:
            raise Failure("HTTP request failed or timed out; verify server access") from None
        self.remaining()
        statuses = (expected,) if isinstance(expected, int) else expected
        require(response.status_code in statuses,
                f"{method} {path.split('?')[0]}: expected HTTP {statuses}, got {response.status_code}")
        if response.status_code == 204:
            require(not response.content, "HTTP 204 must have no response body")
            return None
        try:
            return response.json()
        except ValueError:
            raise Failure("server returned invalid JSON") from None

    def worker_action(self, worker, action, expected=200, token=None):
        return self.request("POST", f"/api/workers/{worker['worker_id']}/{action}",
                            expected, worker, {}, token)

    def heartbeat(self, worker=None):
        require(self.worker_action(worker or self.w2, "heartbeat").get("ok") is True,
                "worker heartbeat was not acknowledged")

    def lease(self, worker):
        if worker is getattr(self, "w2", None) or worker is getattr(self, "w3", None):
            self.heartbeat(worker)
        task = self.worker_action(worker, "lease", (200, 204))
        if task is not None:
            require(task.get("task_type") == "mandelbrot_v1", "unexpected leased task_type")
            require(type(task.get("task_id")) is int and bool(task.get("lease_token")),
                    "malformed lease response")
        return task

    def completion(self, worker, task, result, runtime_ms=0, expected=200):
        body = {"worker_id": worker["worker_id"], "lease_token": task["lease_token"],
                "result": result, "runtime_ms": runtime_ms}
        response = self.request("POST", f"/api/tasks/{task['task_id']}/complete",
                                expected, worker, body)
        if expected == 200:
            require(response.get("accepted") is True, "completion was not accepted")

    def detail(self, job_id):
        return self.request("GET", f"/api/jobs/{job_id}")

    def prepare_reference(self):
        require((ROOT / "backend" / "mandelbrot.py").is_file(),
                "missing backend/mandelbrot.py from C; integrate it before running this test")
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_precompute_child,
                                  args=(str(ROOT / "backend"), self.quick, writer), daemon=True)
        process.start()
        writer.close()
        try:
            wait_until = time.monotonic() + min(self.args.compute_timeout, self.remaining())
            while not reader.poll(min(0.1, max(0, wait_until - time.monotonic()))):
                require(process.is_alive(), "reference kernel process exited unexpectedly")
                require(time.monotonic() < wait_until,
                        "reference computation timed out; increase --compute-timeout")
            try:
                ok, entries = reader.recv()
            except EOFError:
                raise Failure("reference kernel process exited without results") from None
            require(ok, "reference kernel import/computation failed; check C's compute_tile and split_inputs")
            require(len(entries) == 48, "split_inputs(quick) must produce exactly 48 tiles")
            expected_keys = {"tile_x", "tile_y", "tile_w", "tile_h", "image_width",
                             "image_height", "max_iter", "x_min", "x_max", "y_min", "y_max"}
            for index, (tile, result, runtime) in enumerate(entries):
                require(set(tile) == expected_keys, "split_inputs returned unexpected input fields")
                require(tile["tile_x"] == (index % 8) * 64 and tile["tile_y"] == (index // 8) * 64,
                        "split_inputs order/coordinates differ from contract")
                require(tile["tile_w"] == tile["tile_h"] == 64,
                        "split_inputs returned incorrect tile dimensions")
                require(tile["image_width"] == self.quick["width"]
                        and tile["image_height"] == self.quick["height"]
                        and all(tile[key] == self.quick[key] for key in
                                ("max_iter", "x_min", "x_max", "y_min", "y_max")),
                        "split_inputs returned incorrect image/iteration/region metadata")
                self.cache[(tile["tile_x"], tile["tile_y"])] = (tile, result, runtime)
        finally:
            reader.close()
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        self.remaining()

    def stage1(self):
        require(self.request("GET", "/healthz").get("ok") is True, "healthz must return ok=true")
        presets = self.request("GET", "/api/presets")
        require(isinstance(presets, list), "presets response must be an array")
        quicks = [p for p in presets if isinstance(p, dict) and p.get("id") == "quick"]
        require(len(quicks) == 1 and quicks[0].get("params") == QUICK,
                "quick preset must match CONTRACT v1.2 (48 tiles)")
        self.quick = quicks[0]["params"]
        stats = self.request("GET", "/api/stats")
        require(stats.get("workers") == [] and stats.get("totals", {}).get("active_jobs") == 0
                and stats.get("totals", {}).get("tasks_done") == 0,
                "server is not clean: restart a disposable DB with TG_RESET_DB=1; stop other workers")

    def stage2(self):
        changes = [{"width": -1}, {"max_iter": 999999}, {"width": 513}, {"extra_field": 1}]
        for change in changes:
            self.request("POST", "/api/jobs", 422,
                         body={"task_type": "mandelbrot_v1", "params": {**self.quick, **change}})
        self.request("POST", "/api/jobs", 422, body={"task_type": "python_exec", "params": self.quick})

    def stage3(self):
        registrations = [
            ("Smoke-W1", "native", "dev_smokeshared01", "smoke-shared"),
            ("Smoke-W2", "browser", "dev_smokeshared01", "smoke-shared"),
            ("Smoke-W3", "native", "dev_smokeother001", "smoke-other"),
        ]
        for name, worker_type, device_id, owner in registrations:
            worker = self.request("POST", "/api/workers/register", body={
                "name": name, "worker_type": worker_type, "slots": 1, "has_gpu": False,
                "task_types": ["mandelbrot_v1"], "client_info": "independent HTTP contract smoke test",
                "device_id": device_id, "owner": owner,
            })
            require(bool(worker.get("worker_id")) and bool(worker.get("token")), "malformed worker registration")
            self.workers.append(worker)
            self.worker_devices[worker["worker_id"]] = device_id
            self.worker_tiles[worker["worker_id"]] = 0
            self.device_credit.setdefault(device_id, {"tiles": 0, "iters": 0, "runtime_ms": 0})
        self.w1, self.w2, self.w3 = self.workers
        require(len({w["worker_id"] for w in self.workers}) == 3, "workers must have distinct IDs")
        self.ttl = float(self.w2["lease_ttl_s"])
        require(math.isfinite(self.ttl) and self.ttl > 0, "invalid server lease TTL")
        require(all(float(w["lease_ttl_s"]) == self.ttl for w in self.workers),
                "workers received inconsistent TTLs")
        if self.args.lease_ttl is not None:
            require(self.ttl == self.args.lease_ttl, "registration TTL differs from --lease-ttl")
        require(self.ttl + 2 < self.remaining(), "overall timeout is too short for the registered lease TTL")
        self.worker_action(self.w1, "heartbeat", 401, token="invalid-smoke-token")
        self.worker_action(self.w1, "lease", 401, token=self.w2["token"])
        events = self.request("GET", "/api/events?since=0&limit=1")
        self.event_cursor = int(events["next_since"])
        require(self.event_cursor > 0, "registration should have emitted events")

    def stage4(self):
        self.prepare_reference()
        job = self.request("POST", "/api/jobs", body={"task_type": "mandelbrot_v1", "params": self.quick})
        require(job.get("task_count") == 48 and job.get("status") == "queued", "quick job must start queued with 48 tasks")
        self.job_id = job["job_id"]

    def stage5(self):
        first = self.lease(self.w1)
        require(first is not None, "W1 did not receive its initial quick task")
        self.accept_quick(self.w1, first)
        self.held = self.lease(self.w1)
        require(self.held is not None and self.held["job_id"] == self.job_id, "W1 did not receive a quick task")
        self.held_at = time.monotonic()
        self.held_reference = self.reference(self.held)

    def reference(self, task):
        tile = task["input"]
        reference = self.cache.get((tile["tile_x"], tile["tile_y"]))
        require(reference is not None and reference[0] == tile, "server task input disagrees with C's split_inputs")
        return reference

    def accept_quick(self, worker, task):
        require(task["job_id"] == self.job_id, "another job interfered with the smoke run")
        require(task["task_id"] not in self.completed_ids, "server leased an already completed task")
        if getattr(self, "held", None) and task["task_id"] == self.held["task_id"]:
            require(task["lease_token"] != self.held["lease_token"], "reassignment reused the old lease token")
            self.recovered = True
        _, result, runtime = self.reference(task)
        self.completion(worker, task, result, runtime)
        self.completed_ids.add(task["task_id"])
        self.worker_tiles[worker["worker_id"]] += 1
        credit = self.device_credit[self.worker_devices[worker["worker_id"]]]
        credit["tiles"] += 1
        credit["iters"] += sum(result)
        credit["runtime_ms"] += runtime
        self.last_done = worker, task, result, runtime

    def stage6(self):
        turn = 0
        while True:
            worker = (self.w2, self.w3)[turn % 2]
            turn += 1
            task = self.lease(worker)
            if task is None:
                break
            self.accept_quick(worker, task)
            require(len(self.completed_ids) <= 48, "too many quick tasks")
        if not self.recovered:
            # Slow networks may already have expired/reassigned W1 while draining.
            # Otherwise wait from receipt of W1's lease, with a one-second margin.
            target = self.held_at + self.ttl + 1
            while time.monotonic() < target:
                self.heartbeat()
                remaining_wait = target - time.monotonic()
                if remaining_wait > 0:
                    time.sleep(min(0.25, remaining_wait, self.remaining()))
            task = self.lease(self.w2)
            require(task is not None and task["task_id"] == self.held["task_id"],
                    "W2 did not recover W1's expired task")
            self.accept_quick(self.w2, task)
        require(self.recovered and len(self.completed_ids) == 48, "quick job did not complete exactly 48 unique tasks")
        detail = self.detail(self.job_id)
        require(detail.get("status") == "completed" and detail["counts"]["done"] == 48,
                "quick job was not marked completed")

    def stage7(self):
        _, result, runtime = self.held_reference
        self.completion(self.w1, self.held, result, runtime, 409)

    def stage8(self):
        worker, task, result, runtime = self.last_done
        self.completion(worker, task, result, runtime, 409)
        stats = self.request("GET", "/api/stats")
        require(sum(w["tasks_done"] for w in stats["workers"]) == 48,
                "worker task counters must sum to exactly 48 after stale/duplicate submissions")
        require(stats["totals"]["tasks_done"] == 48, "global task counter must be exactly 48")

    def stage9(self):
        params = {**self.quick, "width": 64, "height": 64, "max_iter": 1}
        self.bad_job = self.request("POST", "/api/jobs", body={"task_type": "mandelbrot_v1", "params": params})["job_id"]
        first = self.lease(self.w2)
        require(first is not None and first["job_id"] == self.bad_job, "missing rejection-test task")
        size = first["input"]["tile_w"] * first["input"]["tile_h"]
        self.completion(self.w2, first, [1] * (size - 1), expected=422)
        second = self.lease(self.w2)
        require(second is not None and second["task_id"] == first["task_id"], "rejected task was not requeued")
        require(second["lease_token"] != first["lease_token"], "rejected task retained its lease token")
        self.completion(self.w2, second, [0] * size, expected=422)
        detail = self.detail(self.bad_job)
        require(detail["counts"]["pending"] == 1 and detail["counts"]["done"] == 0,
                "second rejected task must be pending, not completed")

    def stage_contributions(self):
        # Run before stage10 accepts its extra test tile. At this point the only
        # accepted work is quick's 48 tiles, despite stale/duplicate/rejected posts.
        report = self.request("GET", "/api/contributions")
        require(report.get("unit") == "Miter"
                and report.get("basis") == "sum of per-pixel iterations in accepted, spot-checked results",
                "contribution unit/basis differs from contract")
        rows = report["devices"]
        require(len(rows) == 2 and {row["device_id"] for row in rows} == set(self.device_credit),
                "three registrations must aggregate into exactly two device rows")
        require(all(count > 0 for count in self.worker_tiles.values()),
                "all three simulated workers must contribute accepted quick tiles")
        reference_iters = sum(sum(result) for _, result, _ in self.cache.values())
        require(sum(c["iters"] for c in self.device_credit.values()) == reference_iters,
                "accepted quick work does not match the independently computed reference")
        totals = report["totals"]
        require(totals["devices"] == 2 and totals["tiles_done"] == 48
                and sum(row["tiles_done"] for row in rows) == 48,
                "contributions must count exactly 48 accepted tiles across two devices")
        require(math.isclose(totals["verified_miter"], reference_iters / 1_000_000,
                             rel_tol=0, abs_tol=1e-9),
                "total contribution credit differs from compute_tile iteration sum")
        expected_metadata = {
            "dev_smokeshared01": (2, "Smoke-W2", "smoke-shared", ["browser", "native"]),
            "dev_smokeother001": (1, "Smoke-W3", "smoke-other", ["native"]),
        }
        for row in rows:
            credit = self.device_credit[row["device_id"]]
            workers, name, owner, types = expected_metadata[row["device_id"]]
            require(row["workers"] == workers and row["display_name"] == name
                    and row["owner"] == owner and row["worker_types"] == types,
                    "contribution metadata does not match registration history")
            require(row["tiles_done"] == credit["tiles"] and row["compute_ms"] == credit["runtime_ms"],
                    "device tile/runtime totals were lost or counted more than once")
            require(math.isclose(row["verified_miter"], credit["iters"] / 1_000_000,
                                 rel_tol=0, abs_tol=1e-9),
                    "device credit must include only its accepted reference results")
            require(row["share"] == round(credit["iters"] / reference_iters, 4),
                    "device share differs from its fraction of accepted iterations")
        require([row["device_id"] for row in rows] == sorted(
            self.device_credit, key=lambda device: (-self.device_credit[device]["iters"], device)),
            "contribution rows are not sorted by descending credit")
        require(math.isclose(sum(row["verified_miter"] for row in rows), totals["verified_miter"],
                             rel_tol=0, abs_tol=1e-9),
                "device credit does not sum to reported total credit")

    def stage10(self):
        require(self.worker_action(self.w2, "pause").get("ok") is True, "pause was not acknowledged")
        self.worker_action(self.w2, "lease", 204)
        require(self.worker_action(self.w2, "resume").get("ok") is True, "resume was not acknowledged")
        third = self.lease(self.w2)
        require(third is not None and third["job_id"] == self.bad_job,
                "resume did not permit a new lease; use TG_MAX_ATTEMPTS=3 or higher")
        # Finish the rejection-test job with a valid result (max_iter=1 -> every pixel is 1),
        # so the disconnect check below uses a task that still has attempts left.
        # (The backend fails a *pending* task whose attempts are exhausted — CONTRACT-NOTE
        # in scheduler._reap — so releasing a 3rd-attempt task would correctly end as failed.)
        size = third["input"]["tile_w"] * third["input"]["tile_h"]
        self.completion(self.w2, third, [1] * size, expected=200)
        params = {**self.quick, "width": 64, "height": 64, "max_iter": 1}
        self.release_job = self.request("POST", "/api/jobs",
                                        body={"task_type": "mandelbrot_v1", "params": params})["job_id"]
        self.resumed = self.lease(self.w2)
        require(self.resumed is not None and self.resumed["job_id"] == self.release_job,
                "could not lease the disconnect-test task")

    def stage11(self):
        before = self.detail(self.release_job)["counts"]
        require(before["leased"] == 1, "lease expired before disconnect; rerun with a larger server TTL")
        require(self.worker_action(self.w2, "disconnect").get("ok") is True, "disconnect was not acknowledged")
        self.disconnected.add(self.w2["worker_id"])
        after = self.detail(self.release_job)["counts"]
        require(after["leased"] == before["leased"] - 1 and after["pending"] == before["pending"] + 1,
                "disconnect did not immediately return the held task to pending")

    def stage12(self):
        events = []
        cursor = self.event_cursor
        while True:
            page = self.request("GET", f"/api/events?since={cursor}&limit=500")
            batch = page["events"]
            if not batch:
                break
            ids = [e["id"] for e in batch]
            require(ids == sorted(set(ids)) and ids[0] > cursor, "event cursor ordering is invalid")
            require(page["next_since"] >= ids[-1], "event cursor did not advance")
            events.extend(batch)
            cursor = page["next_since"]
        required = {"lease_expired", "task_rejected", "worker_left", "job_completed"}
        require(required <= {e["type"] for e in events}, "missing one or more required integration events")
        require(any(e["type"] == "lease_expired" and e.get("task_id") == self.held["task_id"] for e in events),
                "no lease_expired event for W1's abandoned task")
        require(any(e["type"] == "job_completed" and e.get("job_id") == self.job_id for e in events),
                "no job_completed event for the quick job")
        require(sum(e["type"] == "task_rejected" and e.get("job_id") == self.bad_job
                    for e in events) == 2,
                "the rejection-test job must emit exactly two task_rejected events")
        require(any(e["type"] == "worker_left" and e.get("worker_id") == self.w2["worker_id"] for e in events),
                "no worker_left event for W2")
        require(any(e["type"] == "lease_released" and e.get("task_id") == self.resumed["task_id"]
                    and e.get("worker_id") == self.w2["worker_id"] for e in events),
                "disconnect was not proven by lease_released (an expiry could hide this bug)")

    def cleanup(self):
        # Best effort, bounded, without hiding the primary result or printing tokens.
        for worker in self.workers:
            if worker["worker_id"] in self.disconnected:
                continue
            try:
                response = self.session.post(
                    self.args.server + f"/api/workers/{worker['worker_id']}/disconnect",
                    json={}, headers={"Authorization": "Bearer " + worker["token"]},
                    timeout=min(2, self.args.request_timeout), allow_redirects=False,
                )
                if response.status_code != 200:
                    print("Cleanup warning: a smoke worker could not be disconnected; reset the disposable DB.")
            except self.requests.RequestException:
                print("Cleanup warning: a smoke worker could not be disconnected; reset the disposable DB.")
        self.session.close()


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="disposable server's http(s) base URL")
    parser.add_argument("--timeout", type=positive, default=180, help="overall seconds (default: 180)")
    parser.add_argument("--request-timeout", type=positive, default=10, help="seconds per HTTP request (default: 10)")
    parser.add_argument("--compute-timeout", type=positive, default=20, help="seconds to precompute all quick tiles (default: 20)")
    parser.add_argument("--lease-ttl", type=positive, help="optional expected server TTL; registration is authoritative")
    args = parser.parse_args()
    try:
        url = urlsplit(args.server)
        url.port  # Validate a non-numeric/out-of-range port before any request.
    except ValueError:
        parser.error("--server contains an invalid host or port")
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("--server must be an http(s) base URL without credentials, query or fragment")
    args.server = args.server.rstrip("/")
    try:
        import requests
    except ImportError:
        print("FAIL: missing requests; run: python -m pip install requests")
        return 1
    smoke = Smoke(args, requests)
    passed = 0
    blocked = False
    try:
        for index, (method, label) in enumerate(STAGES, 1):
            if blocked:
                print(f"FAIL {index:02d}/{len(STAGES)} {label}: blocked by earlier failure")
                continue
            try:
                getattr(smoke, method)()
            except Failure as exc:
                print(f"FAIL {index:02d}/{len(STAGES)} {label}: {exc}")
                blocked = True
            except KeyboardInterrupt:
                print(f"FAIL {index:02d}/{len(STAGES)} {label}: interrupted")
                blocked = True
            except Exception as exc:
                print(f"FAIL {index:02d}/{len(STAGES)} {label}: malformed response or integration error ({type(exc).__name__})")
                blocked = True
            else:
                passed += 1
                print(f"PASS {index:02d}/{len(STAGES)} {label}", flush=True)
    finally:
        smoke.cleanup()
    print(f"Summary: {passed}/{len(STAGES)} PASS; {len(STAGES) - passed}/{len(STAGES)} FAIL")
    print("This verifies HTTP contract behavior; real-device network tests and fault drills remain separate.")
    return 0 if passed == len(STAGES) else 1


if __name__ == "__main__":
    sys.exit(main())
