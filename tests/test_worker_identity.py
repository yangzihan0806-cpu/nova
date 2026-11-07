"""Native identities survive restarts without deriving an ID from hardware."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from workers import native_worker


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    # Never modify HOME: all identity writes stay inside this test's directory.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def args(**changes):
    return SimpleNamespace(**(dict(server="http://localhost:8000/", name="Test native",
                                 slots=3, owner=None, max_tasks=1, crash_after=None,
                                 verbose=False) | changes))


@pytest.fixture
def registrations(monkeypatch):
    payloads = []

    def post(url, *, json, **kwargs):
        assert url == "http://127.0.0.1:8000/api/workers/register"
        payloads.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {
            "worker_id": f"wkr_{len(payloads)}", "token": "test-token",
            "heartbeat_interval_s": 5,
        })

    monkeypatch.setattr(native_worker.requests, "post", post)
    return payloads


def test_registration_keeps_identity_across_sessions_and_trims_owner(isolated_home, registrations):
    first = native_worker.NativeWorker(args(owner="  hyang2  "))
    assert first.register().worker_id == "wkr_1"
    assert first.register().worker_id == "wkr_2"
    second = native_worker.NativeWorker(args(owner="  hyang2  "))
    assert second.register().worker_id == "wkr_3"
    identity = registrations[0].get("device_id")
    assert isinstance(identity, str), "registration needs a persistent device_id"
    assert re.fullmatch(r"dev_[0-9a-f]{16}", identity)
    assert {body["device_id"] for body in registrations} == {identity}
    assert (isolated_home / ".tartangrid" / "device_id").read_text().strip() == identity
    assert all(body["owner"] == "hyang2" for body in registrations)
    assert all(body["slots"] == 3 and body["worker_type"] == "native" for body in registrations)


@pytest.mark.parametrize("owner", [None, "", "   "])
def test_optional_owner_is_not_registered_as_blank(owner, registrations):
    native_worker.NativeWorker(args(owner=owner)).register()
    assert registrations[0].get("device_id"), "optional owner must not disable device identity"
    assert registrations[0].get("owner") is None


def test_existing_valid_identity_is_reused_without_rewriting(isolated_home, registrations):
    identity_file = isolated_home / ".tartangrid" / "device_id"
    identity_file.parent.mkdir()
    identity_file.write_text("dev_existing123\n")
    original_mtime = identity_file.stat().st_mtime_ns
    native_worker.NativeWorker(args()).register()
    assert registrations[0].get("device_id") == "dev_existing123"
    assert identity_file.read_text() == "dev_existing123\n"
    assert identity_file.stat().st_mtime_ns == original_mtime


def test_identity_survives_new_python_processes(isolated_home):
    code = """
import json, sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from workers.native_worker import NativeWorker
with patch.object(Path, 'home', return_value=Path(sys.argv[1])):
    worker = NativeWorker(SimpleNamespace(server='http://127.0.0.1:8000', name='Test', slots=2))
    print(json.dumps(getattr(worker, 'device_id', None)))
"""
    def read_from_new_process():
        return json.loads(subprocess.check_output(
            [sys.executable, "-c", code, str(isolated_home)], text=True,
            cwd=Path(__file__).resolve().parents[1],
        ))
    first = read_from_new_process()
    assert isinstance(first, str), "a new native process needs a device identity"
    assert read_from_new_process() == first


def test_simultaneous_first_start_uses_one_complete_identity(isolated_home):
    barrier = threading.Barrier(12)

    def start(_):
        barrier.wait(timeout=5)
        worker = native_worker.NativeWorker(args(server="http://127.0.0.1:8000"))
        return getattr(worker, "device_id", None)

    with ThreadPoolExecutor(max_workers=12) as pool:
        identities = list(pool.map(start, range(12)))
    assert all(isinstance(identity, str) for identity in identities)
    assert len(set(identities)) == 1
    assert re.fullmatch(r"dev_[0-9a-f]{16}", identities[0])
    assert (isolated_home / ".tartangrid" / "device_id").read_text().strip() == identities[0]
    assert sorted(p.name for p in (isolated_home / ".tartangrid").iterdir()) == ["device_id"]


@pytest.mark.parametrize("existing", [b"broken", b"", b"\xff"])
def test_invalid_existing_file_is_preserved_with_session_fallback(existing, isolated_home, registrations, capsys):
    identity_file = isolated_home / ".tartangrid" / "device_id"
    identity_file.parent.mkdir()
    identity_file.write_bytes(existing)
    worker = native_worker.NativeWorker(args())
    worker.register()
    worker.register()
    identity = registrations[0].get("device_id")
    assert isinstance(identity, str), "invalid storage must not stop registration"
    assert re.fullmatch(r"dev_[0-9a-f]{16}", identity)
    assert registrations[1]["device_id"] == identity
    assert identity_file.read_bytes() == existing
    assert "session-only" in capsys.readouterr().out


def test_unwritable_storage_does_not_stop_registration(isolated_home, registrations, capsys):
    (isolated_home / ".tartangrid").write_text("not a directory")
    worker = native_worker.NativeWorker(args())
    worker.register()
    worker.register()
    identity = registrations[0].get("device_id")
    assert isinstance(identity, str), "unavailable storage must use a session identity"
    assert re.fullmatch(r"dev_[0-9a-f]{16}", identity)
    assert registrations[1]["device_id"] == identity
    assert "session-only" in capsys.readouterr().out


def test_atomic_publication_failure_cleans_temp_file(isolated_home, registrations, monkeypatch, capsys):
    def unsupported_link(*_args, **_kwargs):
        raise OSError("filesystem does not support hard links")

    monkeypatch.setattr(native_worker.os, "link", unsupported_link)
    native_worker.NativeWorker(args()).register()
    identity = registrations[0].get("device_id")
    assert isinstance(identity, str)
    assert re.fullmatch(r"dev_[0-9a-f]{16}", identity)
    assert list((isolated_home / ".tartangrid").iterdir()) == []
    assert "session-only" in capsys.readouterr().out


@pytest.mark.parametrize("owner, expected", [("  hyang2  ", "hyang2"), ("   ", None)])
def test_cli_accepts_optional_owner(owner, expected, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["native_worker.py", "--server", "http://localhost:8000",
                                    "--name", "Test", "--owner", owner])
    received = []

    def run(worker):
        received.append(worker.args.owner)
        return 0

    monkeypatch.setattr(native_worker.NativeWorker, "run", run)
    assert native_worker.main() == 0
    assert received == [expected]


def test_cli_rejects_owner_over_40_characters_before_start(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["native_worker.py", "--server", "http://localhost:8000",
                                    "--name", "Test", "--owner", "a" * 41])
    with pytest.raises(SystemExit) as error:
        native_worker.main()
    assert error.value.code == 2
    assert "--owner must contain 1–40 characters" in capsys.readouterr().err
