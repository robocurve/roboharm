"""Background rendering and caching of run camera clips."""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tempfile
import threading
from typing import Any

from .collect import (
    Collector,
    Host,
    _sort_time,
    _ssh_base,
    _validate_frames_dir,
    _validate_segment,
)


LOGGER = logging.getLogger(__name__)
CAMERAS = ("left", "top", "right")
_OUTPUT_NAMES = {
    cam: f"scene-0-e0_{cam}_cam.mp4"
    for cam in CAMERAS
}


def _quote_remote_home(path: str) -> str:
    """Shell-quote a remote path while preserving leading-tilde expansion."""

    if not path.startswith("~/"):
        return shlex.quote(path)
    return "~/" + shlex.quote(path[2:])


class ClipRenderer:
    """Render finished runs one at a time and expose their cached clip URLs."""

    def __init__(
        self,
        collector: Collector,
        media_dir: str | Path | None = None,
        *,
        interval: float = 2.0,
    ) -> None:
        self.collector = collector
        self.media_dir = (
            Path(media_dir)
            if media_dir is not None
            else Path(__file__).resolve().parents[2] / "media"
        )
        self.interval = float(interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._memo: dict[tuple[str, str, str], dict[str, str]] = {}
        self._info_memo: dict[tuple[str, str, str], dict[str, float | int | None]] = {}
        self._handled: set[tuple[str, str, str]] = set()
        self._rendering: tuple[str, str, str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._render_loop,
            name="robot-dashboard-clip-renderer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=346.0)

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    @staticmethod
    def _key(host: str, robot: str, name: str) -> tuple[str, str, str]:
        _validate_segment(host, "host")
        _validate_segment(robot, "robot")
        _validate_segment(name, "run name")
        return host, robot, name

    def _run_dir(self, key: tuple[str, str, str]) -> Path:
        host, robot, _name = key
        return self.media_dir / host / robot

    def _clip_paths(self, key: tuple[str, str, str]) -> dict[str, Path]:
        _host, _robot, name = key
        directory = self._run_dir(key)
        return {cam: directory / f"{name}_{cam}.mp4" for cam in CAMERAS}

    def _noclips_path(self, key: tuple[str, str, str]) -> Path:
        return self._run_dir(key) / f"{key[2]}.noclips"

    @staticmethod
    def _urls(key: tuple[str, str, str], present: dict[str, Path]) -> dict[str, str]:
        host, robot, name = key
        return {
            cam: f"/media/{host}/{robot}/{name}_{cam}.mp4"
            for cam in CAMERAS
            if cam in present
        }

    def clips_for(self, host: str, robot: str, name: str) -> dict[str, str]:
        """Return URLs for cache files that currently exist.

        Partial renders are deliberately checked on each call. Complete renders
        and no-clips markers are stable and can be memoized for this process.
        """

        key = self._key(host, robot, name)
        with self._lock:
            memoized = self._memo.get(key)
            if memoized is not None:
                return dict(memoized)

        if self._noclips_path(key).is_file():
            result: dict[str, str] = {}
            with self._lock:
                self._memo[key] = result
            return {}

        paths = self._clip_paths(key)
        present = {cam: path for cam, path in paths.items() if path.is_file()}
        result = self._urls(key, present)
        if len(present) == len(CAMERAS):
            with self._lock:
                self._memo[key] = result
        return dict(result)

    def clip_info(self, host: str, robot: str, name: str) -> dict[str, float | int | None]:
        """Return frame-rate metadata for the first available cached clip."""

        key = self._key(host, robot, name)
        with self._lock:
            memoized = self._info_memo.get(key)
            if memoized is not None:
                return dict(memoized)

        paths = self._clip_paths(key)
        present = {cam: path for cam, path in paths.items() if path.is_file()}
        fallback: dict[str, float | int | None] = {
            "clip_fps": 30.0,
            "frame_count": None,
            "first_step": 0,
        }
        clip_path = next(
            (present[cam] for cam in ("top", "left", "right") if cam in present),
            None,
        )
        result = fallback
        try:
            if clip_path is None:
                raise FileNotFoundError("no cached clip")
            executable = shutil.which("ffprobe")
            if executable is None:
                local_executable = Path.home() / ".local" / "bin" / "ffprobe"
                if not local_executable.is_file():
                    raise FileNotFoundError("ffprobe")
                executable = str(local_executable)
            completed = subprocess.run(
                [
                    executable,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_frames,r_frame_rate",
                    "-of",
                    "json",
                    str(clip_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            document = json.loads(completed.stdout)
            if not isinstance(document, dict):
                raise ValueError("invalid ffprobe output")
            streams = document.get("streams")
            if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
                raise ValueError("ffprobe returned no video stream")
            stream = streams[0]
            rate_value = stream["r_frame_rate"]
            if isinstance(rate_value, bool) or not isinstance(rate_value, (str, int, float)):
                raise ValueError("invalid frame rate")
            rate_text = str(rate_value)
            if rate_text.count("/") == 1:
                numerator_text, denominator_text = rate_text.split("/")
                numerator = float(numerator_text)
                denominator = float(denominator_text)
                if numerator <= 0 or denominator <= 0:
                    raise ValueError("invalid frame-rate fraction")
                clip_fps = numerator / denominator
            elif "/" not in rate_text:
                clip_fps = float(rate_text)
            else:
                raise ValueError("invalid frame-rate fraction")
            if not math.isfinite(clip_fps) or clip_fps <= 0:
                raise ValueError("invalid clip metadata")
            # nb_frames can be "N/A" or absent (fragmented MP4s); a valid fps
            # is still worth keeping, the page clamps to total_steps instead.
            frame_count: int | None
            try:
                frame_count = int(stream["nb_frames"])
                if frame_count < 0:
                    frame_count = None
            except (KeyError, TypeError, ValueError):
                frame_count = None
            result = {
                "clip_fps": clip_fps,
                "frame_count": frame_count,
                "first_step": 0,
            }
            probed = True
        except Exception:
            result = fallback
            probed = False

        # Only a successful probe of a complete three-camera set is stable
        # enough to memoize; a transient ffprobe failure must be retried.
        if probed and len(present) == len(CAMERAS):
            with self._lock:
                self._info_memo[key] = result
        return dict(result)

    def status_for(self, host: str, robot: str, name: str) -> str:
        """Return a lightweight UI status for a run's clip cache."""

        key = self._key(host, robot, name)
        if self._noclips_path(key).is_file():
            return "no-clips"
        if any(path.is_file() for path in self._clip_paths(key).values()):
            return "ready"
        with self._lock:
            return "rendering" if self._rendering == key else "pending"

    def _mark_no_clips(
        self,
        key: tuple[str, str, str],
        *,
        permanent: bool = True,
    ) -> None:
        marker = self._noclips_path(key)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        with self._lock:
            if permanent:
                self._handled.add(key)
            self._memo[key] = {}

    def poll_once(self) -> None:
        """Render uncached, non-live runs from the newest 200 candidates."""

        snapshot = self.collector.snapshot()
        runs = snapshot.get("runs", [])
        if not isinstance(runs, list):
            return
        candidates = sorted(
            (
                run
                for run in runs
                if isinstance(run, dict)
                and not bool(run.get("live"))
                and (run.get("status") != "started" or not bool(run.get("live")))
            ),
            key=_sort_time,
            reverse=True,
        )[:200]
        if not self._render_lock.acquire(blocking=False):
            return
        try:
            for run in candidates:
                if self._stop.is_set():
                    break
                try:
                    key = self._key(run["host"], run["robot"], run["name"])
                except (KeyError, TypeError, ValueError):
                    continue
                with self._lock:
                    if key in self._handled:
                        continue
                if any(path.is_file() for path in self._clip_paths(key).values()):
                    with self._lock:
                        self._handled.add(key)
                    continue
                host = self.collector.host(key[0])
                permanent = host is None or host.ssh is not None
                frames_dir = run.get("frames_dir")
                if not isinstance(frames_dir, str) or not frames_dir:
                    # In particular, do not try to render abandoned *.live logs
                    # which never recorded a frames directory. Nothing can ever
                    # become adoptable for such a run, so the marker is permanent
                    # on every host.
                    self._mark_no_clips(key)
                    continue
                try:
                    _validate_frames_dir(frames_dir)
                except ValueError:
                    self._mark_no_clips(key)
                    continue

                if host is not None and host.ssh is None:
                    sources = self._adoptable_local(host, run)
                    if sources and self._adopt_local(sources, key):
                        continue

                if self._noclips_path(key).is_file():
                    if permanent:
                        with self._lock:
                            self._handled.add(key)
                    continue

                with self._lock:
                    self._rendering = key
                try:
                    rendered = self._render_run(run, key)
                except Exception as exc:
                    LOGGER.warning("clip render failed for %s/%s/%s: %s", *key, exc)
                    rendered = False
                finally:
                    with self._lock:
                        self._rendering = None
                if rendered:
                    with self._lock:
                        self._handled.add(key)
                else:
                    self._mark_no_clips(key, permanent=permanent)
        finally:
            self._render_lock.release()

    def _render_run(self, run: dict[str, Any], key: tuple[str, str, str]) -> bool:
        host = self.collector.host(key[0])
        if host is None:
            return False
        directory = self._run_dir(key)
        directory.mkdir(parents=True, exist_ok=True)
        if host.ssh is None:
            return self._render_local(host, run, key)
        return self._render_remote(host, run, key)

    def _render_local(
        self,
        host: Host,
        run: dict[str, Any],
        key: tuple[str, str, str],
    ) -> bool:
        _host_name, robot, name = key
        frames = self._frames_path(host, run)
        if not frames.is_dir() or not any(frames.glob("scene-0-e0_*_cam_*.npy")):
            return False

        robot_root = Path(host.root) / robot
        log_path = robot_root / "logs" / f"{name}.json"
        preferred = Path(host.root) / "shared" / ".venv" / "bin" / "inspect-robots"
        executable = str(preferred) if preferred.is_file() else "inspect-robots"
        with tempfile.TemporaryDirectory(prefix="robot-dashboard-clips-") as scratch_value:
            scratch = Path(scratch_value)
            command = [
                "nice", "-n", "10", executable, "video", str(log_path),
                "--out", str(scratch), "--fps", "30",
            ]
            subprocess.run(
                command,
                cwd=robot_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
            return self._store_local_outputs(scratch, key)

    @staticmethod
    def _frames_path(host: Host, run: dict[str, Any]) -> Path:
        robot = run["robot"]
        return Path(host.root).joinpath(
            robot,
            *_validate_frames_dir(run["frames_dir"]).parts,
        )

    def _adoptable_local(
        self,
        host: Host,
        run: dict[str, Any],
    ) -> dict[str, Path] | None:
        if host.ssh is not None:
            return None
        frames = self._frames_path(host, run)
        sources: dict[str, Path] = {}
        for cam in CAMERAS:
            candidate = frames / _OUTPUT_NAMES[cam]
            if candidate.is_file() and candidate.stat().st_size > 0:
                sources[cam] = candidate
        return sources or None

    def _adopt_local(
        self,
        sources: dict[str, Path],
        key: tuple[str, str, str],
    ) -> bool:
        self._run_dir(key).mkdir(parents=True, exist_ok=True)
        targets = self._clip_paths(key)
        copied = False
        for cam, source in sources.items():
            target = targets[cam]
            temporary = target.with_name(target.name + ".tmp")
            temporary.unlink(missing_ok=True)
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copyfile(source, temporary)
            temporary.replace(target)
            copied = True
        self._noclips_path(key).unlink(missing_ok=True)
        with self._lock:
            self._memo.pop(key, None)
            self._info_memo.pop(key, None)
            self._handled.add(key)
        return copied

    def _store_local_outputs(self, scratch: Path, key: tuple[str, str, str]) -> bool:
        targets = self._clip_paths(key)
        copied = False
        for cam, output_name in _OUTPUT_NAMES.items():
            source = scratch / output_name
            if not source.is_file():
                continue
            target = targets[cam]
            temporary = target.with_name(target.name + ".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(target)
            copied = True
        return copied

    @staticmethod
    def _remote_scratch(robot: str, name: str) -> str:
        # robot/name are already validated, so this form safely retains tilde
        # expansion while the configured root and log paths are shell-quoted.
        return f"~/.cache/robot-dashboard/{robot}/{name}"

    @staticmethod
    def _quote_remote_home(path: str) -> str:
        return _quote_remote_home(path)

    def _render_remote(
        self,
        host: Host,
        run: dict[str, Any],
        key: tuple[str, str, str],
    ) -> bool:
        _host_name, robot, name = key
        frames_remote = str(
            PurePosixPath(host.root)
            / robot
            / _validate_frames_dir(run["frames_dir"])
        )
        packed = subprocess.run(
            _ssh_base(host) + [f"ls -1 -- {shlex.quote(frames_remote)}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        listed = set(packed.stdout.splitlines())
        packed_outputs = {
            cam: output_name
            for cam, output_name in _OUTPUT_NAMES.items()
            if output_name in listed
        }
        if packed_outputs:
            targets = self._clip_paths(key)
            copied = False
            for cam, output_name in packed_outputs.items():
                remote_path = f"{frames_remote}/{output_name}"
                fetched = subprocess.run(
                    _ssh_base(host) + [f"cat -- {shlex.quote(remote_path)}"],
                    capture_output=True,
                    timeout=120,
                    check=True,
                )
                target = targets[cam]
                temporary = target.with_name(target.name + ".tmp")
                temporary.write_bytes(fetched.stdout)
                temporary.replace(target)
                copied = True
            return copied

        robot_root = str(PurePosixPath(host.root) / robot)
        log_path = str(PurePosixPath(robot_root) / "logs" / f"{name}.json")
        executable = str(PurePosixPath(host.root) / "shared/.venv/bin/inspect-robots")
        scratch = self._remote_scratch(robot, name)
        scratch_arg = self._quote_remote_home(scratch)
        remote_command = " && ".join(
            (
                f"cd {shlex.quote(robot_root)}",
                f"mkdir -p {scratch_arg}",
                " ".join(
                    (
                        "nice -n 10",
                        shlex.quote(executable),
                        "video",
                        shlex.quote(log_path),
                        "--out",
                        scratch_arg,
                        "--fps 30",
                    )
                ),
                f"ls {scratch_arg}",
            )
        )
        completed = subprocess.run(
            _ssh_base(host) + [remote_command],
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
        listed = set(completed.stdout.splitlines())
        targets = self._clip_paths(key)
        copied = False
        for cam, output_name in _OUTPUT_NAMES.items():
            if output_name not in listed:
                continue
            remote_path = f"{scratch}/{output_name}"
            fetched = subprocess.run(
                _ssh_base(host) + [f"cat -- {self._quote_remote_home(remote_path)}"],
                capture_output=True,
                timeout=120,
                check=True,
            )
            target = targets[cam]
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(fetched.stdout)
            temporary.replace(target)
            copied = True
        return copied
