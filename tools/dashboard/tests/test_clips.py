from __future__ import annotations

import os
from pathlib import Path
import subprocess

from roboharm_dashboard.clips import ClipRenderer
from roboharm_dashboard.collect import Host


class FakeCollector:
    def __init__(self, host: Host, runs: list[dict]) -> None:
        self._host = host
        self._runs = runs

    def snapshot(self) -> dict:
        return {"generated_at": "", "hosts": [], "runs": self._runs}

    def host(self, name: str) -> Host | None:
        return self._host if name == self._host.name else None


def run_summary(host: str = "local") -> dict:
    return {
        "host": host,
        "robot": "robot-main",
        "name": "finished",
        "path": "/unused/robot-main/logs/finished.json",
        "status": "success",
        "live": False,
        "frames_dir": "frames/finished",
        "mtime": 100.0,
    }


def prepare_local_run(root: Path) -> None:
    frames = root / "robot-main" / "frames" / "finished"
    frames.mkdir(parents=True)
    (frames / "scene-0-e0_top_cam_000001.npy").write_bytes(b"frame")
    (root / "robot-main" / "logs").mkdir()


def test_local_render_uses_low_priority_command_cwd_and_cache(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    executable = root / "shared" / ".venv" / "bin" / "inspect-robots"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    run = run_summary()
    collector = FakeCollector(Host("local", str(root)), [run])
    renderer = ClipRenderer(collector, tmp_path / "media")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        scratch = Path(command[command.index("--out") + 1])
        for cam in ("left", "top", "right"):
            (scratch / f"scene-0-e0_{cam}_cam.mp4").write_bytes(cam.encode())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    renderer.poll_once()

    command, kwargs = calls[0]
    assert command == [
        "nice", "-n", "10", str(executable), "video",
        str(root / "robot-main" / "logs" / "finished.json"),
        "--out", command[7], "--fps", "30",
    ]
    assert kwargs["cwd"] == root / "robot-main"
    assert kwargs["timeout"] == 300
    assert (tmp_path / "media/local/robot-main/finished_left.mp4").read_bytes() == b"left"
    assert renderer.clips_for("local", "robot-main", "finished") == {
        "left": "/media/local/robot-main/finished_left.mp4",
        "top": "/media/local/robot-main/finished_top.mp4",
        "right": "/media/local/robot-main/finished_right.mp4",
    }


def test_local_render_falls_back_to_path_binary(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    collector = FakeCollector(Host("local", str(root)), [run_summary()])
    renderer = ClipRenderer(collector, tmp_path / "media")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        scratch = Path(command[command.index("--out") + 1])
        (scratch / "scene-0-e0_top_cam.mp4").write_bytes(b"top")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    renderer.poll_once()

    assert commands[0][3] == "inspect-robots"
    assert (tmp_path / "media/local/robot-main/finished_top.mp4").read_bytes() == b"top"


def test_clip_info_reads_cached_top_clip_with_ffprobe(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(tmp_path)), []),
        tmp_path / "media",
    )
    directory = tmp_path / "media" / "local" / "robot-main"
    directory.mkdir(parents=True)
    for cam in ("left", "top", "right"):
        (directory / f"finished_{cam}.mp4").write_bytes(b"video")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ffprobe = binaries / "ffprobe"
    ffprobe.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"streams\":[{\"nb_frames\":\"3601\",\"r_frame_rate\":\"30000/1001\"}]}'\n",
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))

    assert renderer.clip_info("local", "robot-main", "finished") == {
        "clip_fps": 30000 / 1001,
        "frame_count": 3601,
        "first_step": 0,
    }


def test_clip_info_falls_back_without_ffprobe(tmp_path: Path, monkeypatch) -> None:
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(tmp_path)), []),
        tmp_path / "media",
    )
    directory = tmp_path / "media" / "local" / "robot-main"
    directory.mkdir(parents=True)
    (directory / "finished_top.mp4").write_bytes(b"video")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))

    assert renderer.clip_info("local", "robot-main", "finished") == {
        "clip_fps": 30.0,
        "frame_count": None,
        "first_step": 0,
    }


def test_clip_info_falls_back_for_zero_fraction(tmp_path: Path, monkeypatch) -> None:
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(tmp_path)), []),
        tmp_path / "media",
    )
    directory = tmp_path / "media" / "local" / "robot-main"
    directory.mkdir(parents=True)
    (directory / "finished_top.mp4").write_bytes(b"video")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ffprobe = binaries / "ffprobe"
    ffprobe.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"streams\":[{\"nb_frames\":\"3601\",\"r_frame_rate\":\"0/0\"}]}'\n",
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))

    assert renderer.clip_info("local", "robot-main", "finished") == {
        "clip_fps": 30.0,
        "frame_count": None,
        "first_step": 0,
    }


def test_remote_render_uses_ssh_scratch_and_fetches_only_mp4s(
    tmp_path: Path, monkeypatch
) -> None:
    run = run_summary("remote")
    host = Host("remote", "/srv/robots", "robot-host")
    renderer = ClipRenderer(FakeCollector(host, [run]), tmp_path / "media")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        remote_command = command[-1]
        if remote_command.startswith("ls -1 --"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if "inspect-robots video" in remote_command:
            output = "scene-0-e0_left_cam.mp4\nscene-0-e0_top_cam.mp4\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        camera = "left" if "left_cam.mp4" in remote_command else "top"
        return subprocess.CompletedProcess(command, 0, camera.encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    renderer.poll_once()

    listing_command, listing_kwargs = calls[0]
    assert listing_command[0] == "ssh"
    assert listing_command[-2] == "robot-host"
    assert "BatchMode=yes" in listing_command
    assert "ConnectTimeout=8" in listing_command
    assert "ControlMaster=auto" in listing_command
    assert "ControlPersist=600" in listing_command
    assert "ls -1 -- /srv/robots/robot-main/frames/finished" in listing_command[-1]
    assert listing_kwargs["timeout"] == 15
    render_command, render_kwargs = calls[1]
    assert render_command[0] == "ssh"
    assert render_command[-2] == "robot-host"
    assert "BatchMode=yes" in render_command
    assert "ConnectTimeout=8" in render_command
    assert "ControlMaster=auto" in render_command
    assert "ControlPersist=600" in render_command
    assert "cd /srv/robots/robot-main" in render_command[-1]
    assert "mkdir -p ~/.cache/robot-dashboard/robot-main/finished" in render_command[-1]
    assert "nice -n 10 /srv/robots/shared/.venv/bin/inspect-robots video" in render_command[-1]
    assert "--out ~/.cache/robot-dashboard/robot-main/finished --fps 30" in render_command[-1]
    assert render_kwargs["timeout"] == 300
    assert len(calls) == 4
    assert all(call[1]["timeout"] == 120 for call in calls[2:])
    assert (tmp_path / "media/remote/robot-main/finished_left.mp4").read_bytes() == b"left"
    assert (tmp_path / "media/remote/robot-main/finished_top.mp4").read_bytes() == b"top"


def test_no_clips_marker_skips_render(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    marker = tmp_path / "media/local/robot-main/finished.noclips"
    marker.parent.mkdir(parents=True)
    marker.touch()

    def unexpected_run(*args, **kwargs):
        raise AssertionError("a marked run must not render")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()

    assert renderer.clips_for("local", "robot-main", "finished") == {}


def test_render_failure_writes_no_clips_marker(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )

    def failed_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="failed")

    monkeypatch.setattr(subprocess, "run", failed_run)
    renderer.poll_once()

    assert (tmp_path / "media/local/robot-main/finished.noclips").is_file()


def test_local_adopts_packed_mp4s(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    frames = root / "robot-main" / "frames" / "finished"
    frames.mkdir(parents=True)
    for cam in ("left", "top", "right"):
        (frames / f"scene-0-e0_{cam}_cam.mp4").write_bytes(cam.encode())
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("packed local clips must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()

    for cam in ("left", "top", "right"):
        assert (tmp_path / f"media/local/robot-main/finished_{cam}.mp4").read_bytes() == cam.encode()
    assert renderer.clips_for("local", "robot-main", "finished") == {
        "left": "/media/local/robot-main/finished_left.mp4",
        "top": "/media/local/robot-main/finished_top.mp4",
        "right": "/media/local/robot-main/finished_right.mp4",
    }


def test_local_adoption_clears_no_clips_marker_and_memo(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    frames = root / "robot-main" / "frames" / "finished"
    frames.mkdir(parents=True)
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    marker = tmp_path / "media/local/robot-main/finished.noclips"
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert renderer.clips_for("local", "robot-main", "finished") == {}
    for cam in ("left", "top", "right"):
        (frames / f"scene-0-e0_{cam}_cam.mp4").write_bytes(cam.encode())

    def unexpected_run(*args, **kwargs):
        raise AssertionError("packed local clips must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()

    assert renderer.clips_for("local", "robot-main", "finished") == {
        "left": "/media/local/robot-main/finished_left.mp4",
        "top": "/media/local/robot-main/finished_top.mp4",
        "right": "/media/local/robot-main/finished_right.mp4",
    }
    assert not marker.exists()


def test_local_no_clips_marker_with_npy_does_not_rerender(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    marker = tmp_path / "media/local/robot-main/finished.noclips"
    marker.parent.mkdir(parents=True)
    marker.touch()
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("a marked local run must not render")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()
    renderer.poll_once()

    assert renderer.clips_for("local", "robot-main", "finished") == {}
    assert marker.is_file()
    assert calls == []


def test_remote_no_clips_marker_stays_permanent(tmp_path: Path, monkeypatch) -> None:
    host = Host("remote", "/srv/robots", "robot-host")
    renderer = ClipRenderer(
        FakeCollector(host, [run_summary("remote")]),
        tmp_path / "media",
    )
    marker = tmp_path / "media/remote/robot-main/finished.noclips"
    marker.parent.mkdir(parents=True)
    marker.touch()
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("a marked remote run must not use ssh")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()
    renderer.poll_once()

    assert marker.is_file()
    assert calls == []


def test_remote_adopts_listed_mp4s(tmp_path: Path, monkeypatch) -> None:
    host = Host("remote", "/srv/robots", "robot-host")
    renderer = ClipRenderer(
        FakeCollector(host, [run_summary("remote")]),
        tmp_path / "media",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        remote_command = command[-1]
        if remote_command.startswith("ls -1 --"):
            output = "".join(
                f"scene-0-e0_{cam}_cam.mp4\n"
                for cam in ("left", "top", "right")
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        camera = next(
            cam for cam in ("left", "top", "right")
            if f"{cam}_cam.mp4" in remote_command
        )
        return subprocess.CompletedProcess(command, 0, camera.encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    renderer.poll_once()

    assert all("video" not in call[0][-1] for call in calls)
    assert all(call[1]["timeout"] == 120 for call in calls[1:])
    for cam in ("left", "top", "right"):
        assert (tmp_path / f"media/remote/robot-main/finished_{cam}.mp4").read_bytes() == cam.encode()


def test_local_render_failure_then_adopts_packed_mp4s(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    prepare_local_run(root)
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )

    def failed_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="failed")

    monkeypatch.setattr(subprocess, "run", failed_run)
    renderer.poll_once()
    marker = tmp_path / "media/local/robot-main/finished.noclips"
    assert marker.is_file()

    frames = root / "robot-main" / "frames" / "finished"
    (frames / "scene-0-e0_top_cam_000001.npy").unlink()
    for cam in ("left", "top", "right"):
        (frames / f"scene-0-e0_{cam}_cam.mp4").write_bytes(cam.encode())

    def unexpected_run(*args, **kwargs):
        raise AssertionError("packed local clips must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    renderer.poll_once()

    for cam in ("left", "top", "right"):
        assert (tmp_path / f"media/local/robot-main/finished_{cam}.mp4").read_bytes() == cam.encode()
    assert renderer.clips_for("local", "robot-main", "finished") == {
        "left": "/media/local/robot-main/finished_left.mp4",
        "top": "/media/local/robot-main/finished_top.mp4",
        "right": "/media/local/robot-main/finished_right.mp4",
    }
    assert not marker.exists()


def test_local_adoption_copies_when_hardlink_fails(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    frames = root / "robot-main" / "frames" / "finished"
    frames.mkdir(parents=True)
    for cam in ("left", "top", "right"):
        (frames / f"scene-0-e0_{cam}_cam.mp4").write_bytes(cam.encode())
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    link_calls = []

    def failed_link(source, target):
        link_calls.append((source, target))
        raise OSError("hardlinks unavailable")

    monkeypatch.setattr(os, "link", failed_link)
    renderer.poll_once()

    assert len(link_calls) == 3
    for cam in ("left", "top", "right"):
        assert (
            tmp_path / f"media/local/robot-main/finished_{cam}.mp4"
        ).read_bytes() == cam.encode()
    assert renderer.clips_for("local", "robot-main", "finished") == {
        "left": "/media/local/robot-main/finished_left.mp4",
        "top": "/media/local/robot-main/finished_top.mp4",
        "right": "/media/local/robot-main/finished_right.mp4",
    }


def test_clip_info_does_not_memoize_a_failed_probe(tmp_path: Path, monkeypatch) -> None:
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(tmp_path)), []),
        tmp_path / "media",
    )
    directory = tmp_path / "media" / "local" / "robot-main"
    directory.mkdir(parents=True)
    for cam in ("left", "top", "right"):
        (directory / f"finished_{cam}.mp4").write_bytes(b"video")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "failed-once"
    ffprobe = binaries / "ffprobe"
    # Fails the first time (transient), succeeds afterwards.
    ffprobe.write_text(
        "#!/bin/sh\n"
        # PATH is replaced below, so only shell builtins are available here.
        f"if [ ! -e {marker} ]; then : > {marker}; exit 1; fi\n"
        "printf '%s\\n' '{\"streams\":[{\"nb_frames\":\"1201\",\"r_frame_rate\":\"30/1\"}]}'\n",
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))

    assert renderer.clip_info("local", "robot-main", "finished")["frame_count"] is None
    assert renderer.clip_info("local", "robot-main", "finished") == {
        "clip_fps": 30.0,
        "frame_count": 1201,
        "first_step": 0,
    }


def test_clip_info_keeps_fps_when_nb_frames_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = ClipRenderer(
        FakeCollector(Host("local", str(tmp_path)), []),
        tmp_path / "media",
    )
    directory = tmp_path / "media" / "local" / "robot-main"
    directory.mkdir(parents=True)
    (directory / "finished_top.mp4").write_bytes(b"video")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ffprobe = binaries / "ffprobe"
    ffprobe.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"streams\":[{\"nb_frames\":\"N/A\",\"r_frame_rate\":\"10/1\"}]}'\n",
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))

    assert renderer.clip_info("local", "robot-main", "finished") == {
        "clip_fps": 10.0,
        "frame_count": None,
        "first_step": 0,
    }
