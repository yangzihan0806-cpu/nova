"""Run a TartanGrid native contributor from the command line."""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import platform
import random
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from mandelbrot import BACKEND, compute_tile


TASK_TYPE = "mandelbrot_v1"


@dataclass(frozen=True)
class Registration:
    worker_id: str
    token: str
    heartbeat_interval: float


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


class NativeWorker:
    def __init__(self, args):
        self.args = args
        self.server = args.server.rstrip("/")
        # On Windows, "localhost" tries IPv6 (::1) first; uvicorn listens on IPv4 only and
        # Windows takes ~2 s to refuse each new connection, adding ~4 s per tile
        # (one lease + one complete). Measured on a team laptop. Use 127.0.0.1 instead.
        parts = urlsplit(self.server)
        if parts.hostname == "localhost":
            netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
            self.server = urlunsplit(parts._replace(netloc=netloc))
            log(f"Using {self.server} instead of localhost (avoids a ~2 s IPv6 delay per request)")
        self.stop = threading.Event()
        self.registration_lock = threading.Lock()
        self.task_lock = threading.Lock()
        self.registration = None
        self.recovered = None
        self.total = 0
        self.in_flight = 0
        self.crash_claimed = False
        self.failed = False

    def post(self, path, body, registration=None, *, draining=False, timeout=2):
        headers = {"Authorization": f"Bearer {registration.token}"} if registration else {}
        delay = 0.5
        while draining or not self.stop.is_set():
            try:
                response = requests.post(
                    self.server + path, json=body, headers=headers,
                    timeout=timeout, allow_redirects=False,
                )
                if response.status_code not in (408, 429) and response.status_code < 500:
                    return response
                reason = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                reason = type(exc).__name__
            log(f"{path}: {reason}")
            if self.stop.wait(delay):
                break
            delay = min(8, delay * 2)
        return None

    def register(self):
        response = self.post("/api/workers/register", {
            "name": self.args.name,
            "worker_type": "native",
            "slots": self.args.slots,
            "has_gpu": False,
            "task_types": [TASK_TYPE],
            "client_info": (
                f"{platform.platform()} / Python {platform.python_version()} / {BACKEND}"
            )[:200],
        })
        if response is None:
            return None
        if response.status_code != 200:
            log(f"Registration failed (HTTP {response.status_code})")
            return None
        try:
            data = response.json()
            registration = Registration(
                data["worker_id"], data["token"], float(data["heartbeat_interval_s"])
            )
            if not registration.heartbeat_interval > 0:
                raise ValueError("Invalid heartbeat interval")
        except (ValueError, TypeError, KeyError):
            log("Invalid registration response")
            return None
        log(f"Registered {registration.worker_id}")
        return registration

    def recover(self, rejected):
        with self.registration_lock:
            if self.registration is not rejected or self.stop.is_set():
                return
            if self.recovered is not rejected:
                new_registration = self.register()
                if new_registration is not None:
                    self.registration = self.recovered = new_registration
                    return
            if not self.stop.is_set():
                log("Worker authentication failed after re-registration")
                self.failed = True
                self.stop.set()

    def heartbeat(self):
        while not self.stop.is_set():
            registration = self.registration
            if self.stop.wait(registration.heartbeat_interval):
                break
            response = self.post(f"/api/workers/{registration.worker_id}/heartbeat", {}, registration)
            if response is not None:
                if response.status_code == 401:
                    self.recover(registration)
                elif response.status_code == 200:
                    with self.registration_lock:
                        if self.registration is registration:
                            self.recovered = None
                else:
                    log(f"Heartbeat failed (HTTP {response.status_code})")

    def reserve(self):
        with self.task_lock:
            max_tasks, crash_after = self.args.max_tasks, self.args.crash_after
            if self.stop.is_set() or (max_tasks is not None and self.total >= max_tasks):
                return None
            if crash_after is not None and self.total >= crash_after:
                if self.crash_claimed:
                    return None
                self.crash_claimed = True
                return "crash"
            if any(limit is not None and self.total + self.in_flight >= limit
                   for limit in (max_tasks, crash_after)):
                return None
            self.in_flight += 1
            return "compute"

    def release(self, reservation):
        with self.task_lock:
            if reservation == "crash":
                self.crash_claimed = False
            else:
                self.in_flight -= 1

    def report_task(self, task, registration):
        task_id = task["task_id"]
        lease_token = task["lease_token"]
        if type(task_id) is not int or not isinstance(lease_token, str):
            raise ValueError("Invalid lease")
        body = {"worker_id": registration.worker_id, "lease_token": lease_token}
        started = time.perf_counter()
        try:
            if task["task_type"] != TASK_TYPE:
                raise ValueError("Unsupported task type")
            result = compute_tile(task["input"])
        except Exception as exc:
            body["error"] = f"{type(exc).__name__}: {exc}"[:500]
            action = "fail"
            log(f"Task {task_id} failed to compute ({type(exc).__name__})")
        else:
            body.update(result=result, runtime_ms=round((time.perf_counter() - started) * 1000))
            action = "complete"

        response = self.post(f"/api/tasks/{task_id}/{action}", body, registration, draining=True)
        if response is None:
            return
        status = response.status_code
        if status == 401:
            # This lease belongs to the old registration even if recovery succeeds.
            self.recover(registration)
        elif status == 409:
            log(f"task {task_id}: stale lease, dropped")
        elif status == 422:
            log(f"Warning: task {task_id} was rejected (HTTP 422)")
        elif status != 200:
            log(f"Task {task_id} {action} failed (HTTP {status})")
        elif action == "complete":
            with self.task_lock:
                self.total += 1
                log(f"task {task_id} ({task['job_id']}) done in "
                    f"{body['runtime_ms'] / 1000:.2f}s  [total {self.total}]")
                if self.total == self.args.max_tasks:
                    self.stop.set()

    def run_slot(self):
        idle_streak = 0
        while not self.stop.is_set():
            reservation = self.reserve()
            if reservation is None:
                self.stop.wait(0.05)
                continue
            delay = 0
            try:
                registration = self.registration
                response = self.post(f"/api/workers/{registration.worker_id}/lease", {}, registration)
                if response is None:
                    return
                status = response.status_code
                if status == 200:
                    idle_streak = 0
                    if reservation == "crash":
                        log("Simulated crash after taking a lease")
                        os._exit(1)
                    try:
                        task = response.json()
                        self.report_task(task, registration)
                    except (ValueError, TypeError, KeyError):
                        log("Invalid lease response")
                        delay = 0.5
                elif status == 204:
                    delay = min(3, random.uniform(0.5, 1.0) + idle_streak * 0.25)
                    idle_streak = min(10, idle_streak + 1)
                    if self.args.verbose:
                        log(f"No lease available; retrying in {delay:.2f}s")
                elif status == 401:
                    self.recover(registration)
                else:
                    log(f"Lease failed (HTTP {status})")
                    delay = 0.5
            finally:
                self.release(reservation)
            self.stop.wait(delay)

    def run(self):
        threads = []
        try:
            log(f"Kernel backend: {BACKEND}")
            if BACKEND == "numba":
                compute_tile(dict(tile_x=0, tile_y=0, tile_w=1, tile_h=1, image_width=1,
                                  image_height=1, max_iter=1, x_min=-2.2, x_max=1.0,
                                  y_min=-1.2, y_max=1.2))
            self.registration = self.register()
            if self.registration is None:
                return 1
            threads = [
                threading.Thread(target=self.heartbeat, daemon=True),
                *(threading.Thread(target=self.run_slot, daemon=True) for _ in range(self.args.slots)),
            ]
            for thread in threads:
                thread.start()
            self.stop.wait()
        except KeyboardInterrupt:
            log("Stopping worker")
        finally:
            self.stop.set()
            deadline = time.monotonic() + 5
            for thread in threads:
                if thread.ident is not None:
                    thread.join(max(0, deadline - time.monotonic()))
            registration = self.registration
            if registration:
                response = self.post(
                    f"/api/workers/{registration.worker_id}/disconnect", {},
                    registration, draining=True, timeout=1,
                )
                if response is None or response.status_code != 200:
                    log("Disconnect was not acknowledged")
        return int(self.failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--slots", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--crash-after", type=int)
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(args.name) <= 40:
        parser.error("--name must contain 1–40 characters")
    if not args.server.startswith(("http://", "https://")):
        parser.error("--server must be an HTTP or HTTPS URL")
    if args.max_tasks is not None and args.max_tasks < 1:
        parser.error("--max-tasks must be positive")
    if args.crash_after is not None and args.crash_after < 0:
        parser.error("--crash-after must be nonnegative")
    return NativeWorker(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
