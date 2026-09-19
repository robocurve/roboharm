from __future__ import annotations

import os
from pathlib import Path
import subprocess

from roboharm_dashboard.collect import Host
from roboharm_dashboard.transcripts import TranscriptRenderer


class FakeCollector:
    def __init__(self, host: Host, runs: list[dict]) -> None:
        self._host = host
        self._runs = runs

    def snapshot(self) -> dict:
        return {"generated_at": "", "hosts": [], "runs": self._runs}

    def host(self, name: str) -> Host | None:
        return self._host if name == self._host.name else None


def run_summary(host: str = "local", *, mtime: float = 100.0) -> dict:
    return {
        "host": host,
        "robot": "robot-main",
        "name": "finished",
        "status": "success",
        "live": False,
        "mtime": mtime,
    }


def prepare_local_root(root: Path) -> Path:
    (root / "robot-main" / "logs").mkdir(parents=True)
    (root / "robot-main" / "logs" / "finished.json").write_text(
        "{}", encoding="utf-8"
    )
    executable = root / "shared" / ".venv" / "bin" / "inspect-robots"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    return executable


def test_local_render_builds_command_cwd_and_cache(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    executable = prepare_local_root(root)
    renderer = TranscriptRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("-o") + 1])
        output.write_text("<html>transcript</html>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    path = renderer.render("local", "robot-main", "finished")

    assert path == tmp_path / "media" / "local" / "robot-main" / "finished.html"
    assert path.read_text(encoding="utf-8") == "<html>transcript</html>"
    command, kwargs = calls[0]
    assert command == [
        str(executable),
        "view",
        str(root / "robot-main" / "logs" / "finished.json"),
        "-o",
        command[4],
        "--no-video",
        "--frames-budget",
        "12",
    ]
    assert Path(command[4]).parent == tmp_path / "media" / "local" / "robot-main"
    assert kwargs["cwd"] == root / "robot-main"
    assert kwargs["timeout"] == 120
    assert kwargs["check"] is True


def test_cache_hit_skips_subprocess(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    prepare_local_root(root)
    renderer = TranscriptRenderer(
        FakeCollector(Host("local", str(root)), [run_summary()]),
        tmp_path / "media",
    )
    cache = tmp_path / "media" / "local" / "robot-main" / "finished.html"
    cache.parent.mkdir(parents=True)
    cache.write_text("cached", encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("a current transcript must not be rendered again")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    assert renderer.render("local", "robot-main", "finished") == cache


def test_stale_cache_rerenders(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    prepare_local_root(root)
    renderer = TranscriptRenderer(
        FakeCollector(Host("local", str(root)), [run_summary(mtime=1_000.0)]),
        tmp_path / "media",
    )
    cache = tmp_path / "media" / "local" / "robot-main" / "finished.html"
    cache.parent.mkdir(parents=True)
    cache.write_text("old", encoding="utf-8")
    os.utime(cache, (500.0, 500.0))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[command.index("-o") + 1]).write_text("new", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert renderer.render("local", "robot-main", "finished") == cache
    assert cache.read_text(encoding="utf-8") == "new"
    assert len(calls) == 1


def test_remote_render_uses_remote_cache_and_fetches_html(
    tmp_path: Path, monkeypatch
) -> None:
    host = Host("remote", "/srv/robots", "robot-host")
    renderer = TranscriptRenderer(
        FakeCollector(host, [run_summary("remote")]),
        tmp_path / "media",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "cat --" in command[-1]:
            return subprocess.CompletedProcess(command, 0, b"<html>remote</html>", b"")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    path = renderer.render("remote", "robot-main", "finished")

    assert path == tmp_path / "media" / "remote" / "robot-main" / "finished.html"
    assert path.read_bytes() == b"<html>remote</html>"
    render_command, render_kwargs = calls[0]
    assert render_command[0] == "ssh"
    assert render_command[-2] == "robot-host"
    assert "BatchMode=yes" in render_command
    assert "ConnectTimeout=8" in render_command
    assert "ControlMaster=auto" in render_command
    assert "ControlPersist=600" in render_command
    assert "cd /srv/robots/robot-main" in render_command[-1]
    assert "mkdir -p ~/.cache/robot-dashboard/robot-main/finished" in render_command[-1]
    assert "/srv/robots/shared/.venv/bin/inspect-robots view" in render_command[-1]
    assert "-o ~/.cache/robot-dashboard/robot-main/finished/view.html" in render_command[-1]
    assert "--no-video --frames-budget 12" in render_command[-1]
    assert render_kwargs["timeout"] == 120
    assert calls[1][0][-1] == (
        "cat -- ~/.cache/robot-dashboard/robot-main/finished/view.html"
    )
    assert calls[1][1]["timeout"] == 120
