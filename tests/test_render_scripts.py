from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def wait_for_file(path: Path) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {path}")


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for_process_exit(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            return
        time.sleep(0.05)
    pytest.fail(f"Process {pid} survived shutdown")


def stop_process(pid: int) -> None:
    if process_is_running(pid):
        os.kill(pid, signal.SIGTERM)
        wait_for_process_exit(pid)


def stop_service(process: subprocess.Popen[object]) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)


def test_render_start_stops_background_workers_on_shutdown(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    binaries = tmp_path / "bin"
    scripts.mkdir()
    binaries.mkdir()
    bootstrap_pid_file = tmp_path / "bootstrap.pid"
    scheduler_pid_file = tmp_path / "scheduler.pid"
    server_pid_file = tmp_path / "server.pid"

    write_executable(
        scripts / "render-bootstrap.sh",
        """#!/bin/sh
printf '%s\\n' "$$" > "$BOOTSTRAP_PID_FILE"
trap 'exit 0' HUP INT TERM
while :; do sleep 1; done
""",
    )
    write_executable(
        binaries / "aifpl",
        """#!/bin/sh
printf '%s\\n' "$$" > "$SCHEDULER_PID_FILE"
trap 'exit 0' HUP INT TERM
while :; do sleep 1; done
""",
    )
    write_executable(
        binaries / "uvicorn",
        """#!/bin/sh
printf '%s\\n' "$$" > "$SERVER_PID_FILE"
trap 'exit 0' HUP INT TERM
while :; do sleep 1; done
""",
    )
    environment = os.environ | {
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "BOOTSTRAP_PID_FILE": str(bootstrap_pid_file),
        "SCHEDULER_PID_FILE": str(scheduler_pid_file),
        "SERVER_PID_FILE": str(server_pid_file),
    }
    service = subprocess.Popen(
        ["sh", str(ROOT / "scripts" / "render-start.sh")],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids: list[int] = []
    try:
        pids = [
            int(wait_for_file(bootstrap_pid_file)),
            int(wait_for_file(scheduler_pid_file)),
            int(wait_for_file(server_pid_file)),
        ]
        stop_service(service)
        for pid in pids:
            wait_for_process_exit(pid)
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=5)
        for pid in pids:
            stop_process(pid)


def test_render_bootstrap_stops_its_active_command_on_shutdown(tmp_path: Path) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    child_pid_file = tmp_path / "child.pid"
    write_executable(
        binaries / "aifpl",
        """#!/bin/sh
printf '%s\\n' "$$" > "$CHILD_PID_FILE"
trap 'exit 0' HUP INT TERM
while :; do sleep 1; done
""",
    )
    environment = os.environ | {
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "AIFPL_DATA_DIR": str(tmp_path / "data"),
        "CHILD_PID_FILE": str(child_pid_file),
    }
    bootstrap = subprocess.Popen(
        ["sh", str(ROOT / "scripts" / "render-bootstrap.sh")],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid: int | None = None
    try:
        child_pid = int(wait_for_file(child_pid_file))
        stop_service(bootstrap)
        wait_for_process_exit(child_pid)
    finally:
        if bootstrap.poll() is None:
            bootstrap.kill()
            bootstrap.wait(timeout=5)
        if child_pid is not None:
            stop_process(child_pid)
