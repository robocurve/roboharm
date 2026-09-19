"""Concurrent local and SSH collection for the robot dashboard."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import threading
import time
import tomllib
from typing import Any

import numpy as np
from PIL import Image

from . import summarize


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CAMERAS = frozenset({"left_cam", "top_cam", "right_cam"})


@dataclass(frozen=True, slots=True)
class Host:
    name: str
    root: str
    ssh: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_time(run: dict[str, Any]) -> float:
    started = run.get("started_at")
    if isinstance(started, str):
        try:
            return datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        return float(run.get("mtime") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _validate_segment(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"unsafe {label}")


def _validate_frames_dir(value: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("unsafe frames_dir")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise ValueError("unsafe frames_dir")
    if any(part in {"", ".", ".."} or not _SAFE_NAME.fullmatch(part) for part in path.parts):
        raise ValueError("unsafe frames_dir")
    return path


def _ssh_base(host: Host) -> list[str]:
    if not host.ssh:
        raise ValueError("host is local")
    # One persistent multiplexed connection per host: a fresh ssh handshake on
    # every poll trips the remote sshd's PerSourcePenalties and gets us blocked.
    return [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/sb-dashboard-ssh-%r@%h:%p",
        "-o", "ControlPersist=600",
        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
        host.ssh,
    ]


class Collector:
    """Poll all configured hosts and retain a lock-protected last-good view."""

    def __init__(
        self,
        hosts_path: str | Path = "hosts.toml",
        *,
        limit: int = 0,
        interval: float = 2.0,
    ) -> None:
        self.hosts_path = Path(hosts_path)
        self.limit = int(limit)
        self.interval = float(interval)
        self.hosts = self._read_hosts(self.hosts_path)
        self.archive_instructions = self._read_archive(self.hosts_path)
        self._hosts_by_name = {host.name: host for host in self.hosts}
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {
            host.name: {"runs": [], "error": None, "stale_since": None}
            for host in self.hosts
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _read_archive(path: Path) -> frozenset[str]:
        """Return instructions to hide from the dashboard, from ``archive_instructions``.

        An archived task stays on disk and keeps its videos; it is only dropped
        from the served run list so a labeler is not asked to grade a task that
        was retired. Absent or malformed config means nothing is hidden, which
        keeps a config typo from silently shrinking the dataset.
        """

        try:
            with path.open("rb") as file:
                document = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError):
            return frozenset()
        values = document.get("archive_instructions")
        if not isinstance(values, list):
            return frozenset()
        return frozenset(v for v in values if isinstance(v, str) and v)

    @staticmethod
    def _read_hosts(path: Path) -> tuple[Host, ...]:
        with path.open("rb") as file:
            document = tomllib.load(file)
        entries = document.get("host")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{path} must contain at least one [[host]]")
        hosts: list[Host] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("invalid [[host]] entry")
            name = entry.get("name")
            root = entry.get("root")
            ssh = entry.get("ssh")
            _validate_segment(name, "host name")
            if name in seen:
                raise ValueError(f"duplicate host name: {name}")
            if not isinstance(root, str) or not root:
                raise ValueError(f"host {name} has no root")
            if ssh is not None and (not isinstance(ssh, str) or not ssh):
                raise ValueError(f"host {name} has an invalid ssh value")
            seen.add(name)
            hosts.append(Host(name=name, root=root, ssh=ssh))
        return tuple(hosts)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="robot-dashboard-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, min(20.0, self.interval + 16.0)))

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.poll_once()
            remaining = max(0.0, self.interval - (time.monotonic() - started))
            self._stop.wait(remaining)

    def _scan_host(self, host: Host) -> list[dict[str, Any]]:
        if host.ssh is None:
            runs = summarize.scan(host.root, self.limit)
        else:
            source = Path(summarize.__file__).read_text(encoding="utf-8")
            command = _ssh_base(host) + ["python3", "-", host.root, str(self.limit)]
            completed = subprocess.run(
                command,
                input=source,
                text=True,
                capture_output=True,
                timeout=15,
                check=True,
            )
            value = json.loads(completed.stdout)
            if not isinstance(value, list):
                raise ValueError("remote summarizer did not return a JSON list")
            runs = value

        normalized: list[dict[str, Any]] = []
        for run in runs:
            if isinstance(run, dict):
                copied = dict(run)
                copied["host"] = host.name
                normalized.append(copied)
        return normalized

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        if isinstance(exc, subprocess.TimeoutExpired):
            return "ssh timed out after 15 seconds"
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
            return stderr or f"ssh exited with status {exc.returncode}"
        return str(exc) or exc.__class__.__name__

    def poll_once(self) -> None:
        """Poll every host concurrently and atomically update each host state."""

        if not self.hosts:
            return
        with ThreadPoolExecutor(max_workers=len(self.hosts)) as executor:
            futures = {executor.submit(self._scan_host, host): host for host in self.hosts}
            for future in as_completed(futures):
                host = futures[future]
                try:
                    runs = future.result()
                except Exception as exc:  # one failed host must not stop other hosts
                    error = self._error_text(exc)
                    with self._lock:
                        state = self._states[host.name]
                        state["error"] = error
                        if state["stale_since"] is None:
                            state["stale_since"] = _now_iso()
                else:
                    with self._lock:
                        state = self._states[host.name]
                        state["runs"] = runs
                        state["error"] = None
                        state["stale_since"] = None

    def snapshot(self) -> dict[str, Any]:
        """Return the cached view; this method never performs filesystem or SSH I/O."""

        with self._lock:
            hosts = [
                {
                    "name": host.name,
                    "error": self._states[host.name]["error"],
                    "stale_since": self._states[host.name]["stale_since"],
                }
                for host in self.hosts
            ]
            runs = [
                dict(run)
                for host in self.hosts
                for run in self._states[host.name]["runs"]
                if run.get("instruction") not in self.archive_instructions
            ]
        runs.sort(key=_sort_time, reverse=True)
        return {"generated_at": _now_iso(), "hosts": hosts, "runs": runs}

    def host(self, name: str) -> Host | None:
        return self._hosts_by_name.get(name)

    def latest_frame(
        self,
        host_name: str,
        robot: str,
        frames_dir: str,
        cam: str,
    ) -> tuple[int, bytes] | None:
        """Return ``(frame_index, npy_bytes)`` for the newest matching frame."""

        _validate_segment(host_name, "host")
        _validate_segment(robot, "robot")
        if cam not in _CAMERAS:
            raise ValueError("unsupported camera")
        relative_frames = _validate_frames_dir(frames_dir)
        host = self._hosts_by_name.get(host_name)
        if host is None:
            raise ValueError("unknown host")

        pattern = re.compile(rf"^scene-0-e0_{re.escape(cam)}_(\d+)\.npy$")
        if host.ssh is None:
            directory = Path(host.root).joinpath(robot, *relative_frames.parts)
            newest: tuple[int, Path] | None = None
            for path in directory.glob(f"scene-0-e0_{cam}_*.npy"):
                match = pattern.fullmatch(path.name)
                if match:
                    item = (int(match.group(1)), path)
                    if newest is None or item[0] > newest[0]:
                        newest = item
            if newest is None:
                return None
            return newest[0], newest[1].read_bytes()

        remote_dir = str(PurePosixPath(host.root) / robot / relative_frames)
        prefix = f"scene-0-e0_{cam}_"
        list_command = (
            f"ls -1 {shlex.quote(remote_dir)} 2>/dev/null | "
            f"grep {shlex.quote('^' + prefix)} | sort | tail -n 1"
        )
        listed = subprocess.run(
            _ssh_base(host) + [list_command],
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
        )
        filename = listed.stdout.strip()
        match = pattern.fullmatch(filename)
        if not match:
            return None
        remote_path = f"{remote_dir}/{filename}"
        fetched = subprocess.run(
            _ssh_base(host) + [f"cat -- {shlex.quote(remote_path)}"],
            capture_output=True,
            timeout=15,
            check=True,
        )
        return int(match.group(1)), fetched.stdout

def encode_jpeg(npy_bytes: bytes) -> bytes:
    """Decode an in-memory NPY image and return a quality-80 JPEG."""

    array = np.load(BytesIO(npy_bytes), allow_pickle=False)
    image = Image.fromarray(array)
    output = BytesIO()
    image.save(output, format="JPEG", quality=80)
    return output.getvalue()
