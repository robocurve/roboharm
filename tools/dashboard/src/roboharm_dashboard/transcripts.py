"""On-demand rendering and caching of inspect-robots transcripts."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable

from .clips import _quote_remote_home
from .collect import Collector, Host, _ssh_base, _validate_segment


LOGGER = logging.getLogger(__name__)
_RENDER_THROTTLE_SECONDS = 10.0
_STDERR_TAIL_LENGTH = 2_000


class TranscriptRenderer:
    """Render transcript HTML on demand and retain it in the media cache."""

    def __init__(
        self,
        hosts: Collector | Iterable[Host],
        media_dir: str | Path | None = None,
    ) -> None:
        if callable(getattr(hosts, "snapshot", None)) and callable(
            getattr(hosts, "host", None)
        ):
            self.collector: Any | None = hosts
            self._hosts_by_name: dict[str, Host] = {}
        else:
            self.collector = None
            self._hosts_by_name = {host.name: host for host in hosts}
        self.media_dir = (
            Path(media_dir)
            if media_dir is not None
            else Path(__file__).resolve().parents[2] / "media"
        )
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._attempted_at: dict[tuple[str, str, str], float] = {}
        self._errors: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _key(host: str, robot: str, name: str) -> tuple[str, str, str]:
        _validate_segment(host, "host")
        _validate_segment(robot, "robot")
        _validate_segment(name, "run name")
        return host, robot, name

    def _lock_for(self, key: tuple[str, str, str]) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _cache_path(self, key: tuple[str, str, str]) -> Path:
        host, robot, name = key
        return self.media_dir / host / robot / f"{name}.html"

    def _snapshot_run(self, key: tuple[str, str, str]) -> dict[str, Any] | None:
        if self.collector is None:
            return None
        host, robot, name = key
        snapshot = self.collector.snapshot()
        runs = snapshot.get("runs", [])
        if not isinstance(runs, list):
            return None
        return next(
            (
                run
                for run in runs
                if isinstance(run, dict)
                and run.get("host") == host
                and run.get("robot") == robot
                and run.get("name") == name
            ),
            None,
        )

    @staticmethod
    def _cache_is_valid(cache: Path, run: dict[str, Any] | None) -> bool:
        try:
            cache_mtime = cache.stat().st_mtime
        except OSError:
            return False
        if run is None:
            return True
        try:
            log_mtime = float(run.get("mtime"))
        except (TypeError, ValueError):
            return True
        return cache_mtime >= log_mtime

    def error_for(self, host: str, robot: str, name: str) -> str | None:
        """Return the most recent render failure for a validated run key."""

        key = self._key(host, robot, name)
        with self._locks_guard:
            return self._errors.get(key)

    def render(self, host: str, robot: str, name: str) -> Path | None:
        """Return a current cached transcript, rendering it when necessary."""

        key = self._key(host, robot, name)
        configured_host = (
            self.collector.host(host)
            if self.collector is not None
            else self._hosts_by_name.get(host)
        )
        if configured_host is None:
            with self._locks_guard:
                self._errors[key] = "Unknown host"
            return None

        run = self._snapshot_run(key)
        cache = self._cache_path(key)
        if self._cache_is_valid(cache, run):
            return cache

        with self._lock_for(key):
            # A concurrent request may have completed while this one waited.
            run = self._snapshot_run(key)
            if self._cache_is_valid(cache, run):
                return cache

            now = time.monotonic()
            with self._locks_guard:
                last_attempt = self._attempted_at.get(key)
            if last_attempt is not None and now - last_attempt < _RENDER_THROTTLE_SECONDS:
                with self._locks_guard:
                    self._errors[key] = (
                        "Transcript refresh is throttled; try again in a few seconds."
                    )
                return None
            with self._locks_guard:
                self._attempted_at[key] = now

            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                if configured_host.ssh is None:
                    self._render_local(configured_host, key, cache)
                else:
                    self._render_remote(configured_host, key, cache)
            except Exception as exc:
                reason = self._failure_reason(exc)
                with self._locks_guard:
                    self._errors[key] = reason
                LOGGER.warning(
                    "transcript render failed for %s/%s/%s: %s",
                    *key,
                    reason,
                )
                return None
            with self._locks_guard:
                self._errors.pop(key, None)
            return cache

    @staticmethod
    def _temporary_path(cache: Path) -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{cache.stem}.tmp-",
            suffix=".html",
            dir=cache.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        handle.close()
        temporary.unlink()
        return temporary

    def _render_local(
        self,
        host: Host,
        key: tuple[str, str, str],
        cache: Path,
    ) -> None:
        _host_name, robot, name = key
        robot_root = Path(host.root) / robot
        log_path = robot_root / "logs" / f"{name}.json"
        preferred = Path(host.root) / "shared" / ".venv" / "bin" / "inspect-robots"
        executable = str(preferred) if preferred.is_file() else "inspect-robots"
        temporary = self._temporary_path(cache)
        command = [
            executable,
            "view",
            str(log_path),
            "-o",
            str(temporary),
            "--no-video",
            "--frames-budget",
            "12",
        ]
        try:
            subprocess.run(
                command,
                cwd=robot_root,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
            if not temporary.is_file():
                raise RuntimeError("inspect-robots view did not create HTML output")
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _remote_scratch(robot: str, name: str) -> str:
        return f"~/.cache/robot-dashboard/{robot}/{name}"

    def _render_remote(
        self,
        host: Host,
        key: tuple[str, str, str],
        cache: Path,
    ) -> None:
        _host_name, robot, name = key
        robot_root = str(PurePosixPath(host.root) / robot)
        log_path = str(PurePosixPath(robot_root) / "logs" / f"{name}.json")
        preferred = str(PurePosixPath(host.root) / "shared/.venv/bin/inspect-robots")
        scratch = self._remote_scratch(robot, name)
        scratch_arg = _quote_remote_home(scratch)
        output = f"{scratch}/view.html"
        output_arg = _quote_remote_home(output)
        view_args = " ".join(
            (
                "view",
                shlex.quote(log_path),
                "-o",
                output_arg,
                "--no-video",
                "--frames-budget 12",
            )
        )
        remote_command = " && ".join(
            (
                f"cd {shlex.quote(robot_root)}",
                f"mkdir -p {scratch_arg}",
                (
                    f"if [ -x {shlex.quote(preferred)} ]; then "
                    f"{shlex.quote(preferred)} {view_args}; else "
                    f"inspect-robots {view_args}; fi"
                ),
            )
        )
        subprocess.run(
            _ssh_base(host) + [remote_command],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

        temporary = self._temporary_path(cache)
        try:
            fetched = subprocess.run(
                _ssh_base(host) + [f"cat -- {output_arg}"],
                capture_output=True,
                timeout=120,
                check=True,
            )
            temporary.write_bytes(fetched.stdout)
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _failure_reason(exc: BaseException) -> str:
        if isinstance(exc, subprocess.TimeoutExpired):
            return "inspect-robots view timed out after 120 seconds"
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            tail = stderr.strip()[-_STDERR_TAIL_LENGTH:] if isinstance(stderr, str) else ""
            return tail or f"inspect-robots view exited with status {exc.returncode}"
        return str(exc) or exc.__class__.__name__
