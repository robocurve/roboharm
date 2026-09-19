from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from roboharm_dashboard.collect import Collector  # noqa: E402
from roboharm_dashboard.clips import ClipRenderer  # noqa: E402
from roboharm_dashboard.server import make_server  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


def test_local_server_lists_runs_and_encodes_thumbnail(tmp_path: Path) -> None:
    root = tmp_path / "root"
    logs = root / "robot-main" / "logs"
    logs.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "finished.json", logs / "finished.json")
    shutil.copyfile(FIXTURES / "started.json", logs / "started.json")

    started = json.loads((FIXTURES / "started.json").read_text(encoding="utf-8"))
    frames = root / "robot-main" / started["stats"]["frames_dir"]
    frames.mkdir(parents=True)
    buffer = BytesIO()
    np.save(buffer, np.full((224, 224, 3), [25, 100, 220], dtype=np.uint8))
    (frames / "scene-0-e0_top_cam_000137.npy").write_bytes(buffer.getvalue())

    hosts = tmp_path / "hosts.toml"
    hosts.write_text(
        '[[host]]\nname = "local"\nroot = ' + json.dumps(str(root)) + "\n",
        encoding="utf-8",
    )
    collector = Collector(hosts, limit=50)
    collector.poll_once()
    server = make_server(("127.0.0.1", 0), collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/runs", timeout=5) as response:
            payload = json.load(response)
        assert payload["hosts"] == [{"name": "local", "error": None, "stale_since": None}]
        assert {run["name"] for run in payload["runs"]} == {"finished", "started"}
        assert {
            run["name"]: run["transcript"] for run in payload["runs"]
        } == {
            "finished": "/log/local/robot-main/finished",
            "started": "/log/local/robot-main/started",
        }

        with urlopen(base + "/thumb/local/robot-main/started?cam=top_cam", timeout=5) as response:
            jpeg = response.read()
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.headers["Cache-Control"] == "no-store"
        assert jpeg.startswith(b"\xff\xd8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_local_collector(tmp_path: Path) -> Collector:
    root = tmp_path / "root"
    logs = root / "robot-main" / "logs"
    logs.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "finished.json", logs / "finished.json")
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(
        '[[host]]\nname = "local"\nroot = ' + json.dumps(str(root)) + "\n",
        encoding="utf-8",
    )
    collector = Collector(hosts, limit=50)
    collector.poll_once()
    return collector


def test_media_range_requests_and_invalid_names(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    media = tmp_path / "media"
    clip = media / "local" / "robot-main" / "finished_top.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"0123456789")
    renderer = ClipRenderer(collector, media)
    server = make_server(("127.0.0.1", 0), collector, renderer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/media/local/robot-main/finished_top.mp4", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "video/mp4"
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.headers["Content-Length"] == "10"
            assert response.read() == b"0123456789"

        request = Request(
            base + "/media/local/robot-main/finished_top.mp4",
            headers={"Range": "bytes=2-5"},
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.headers["Content-Length"] == "4"
            assert response.read() == b"2345"

        bad_range = Request(
            base + "/media/local/robot-main/finished_top.mp4",
            headers={"Range": "bytes=20-30"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(bad_range, timeout=5)
        assert error.value.code == 416
        assert error.value.headers["Content-Range"] == "bytes */10"

        with pytest.raises(HTTPError) as error:
            urlopen(base + "/media/local/robot-main/not-a-video.txt", timeout=5)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_runs_includes_existing_clip_urls(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    media = tmp_path / "media"
    clip = media / "local" / "robot-main" / "finished_left.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"video")
    renderer = ClipRenderer(collector, media)
    server = make_server(("127.0.0.1", 0), collector, renderer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/runs", timeout=5) as response:
            payload = json.load(response)
        run = next(item for item in payload["runs"] if item["name"] == "finished")
        assert run["clips"] == {
            "left": "/media/local/robot-main/finished_left.mp4"
        }
        assert run["transcript"] == "/log/local/robot-main/finished"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_route_serves_html_and_rejects_bad_segments(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    server = make_server(("127.0.0.1", 0), collector, media_dir=tmp_path / "media")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/review/local/robot-main/finished", timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert "Synchronized camera videos" in body
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/review/local/robot-main/bad%20name", timeout=5)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_run_returns_enriched_run_and_clip_info(
    tmp_path: Path, monkeypatch
) -> None:
    collector = make_local_collector(tmp_path)
    media = tmp_path / "media"
    clip = media / "local" / "robot-main" / "finished_top.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"video")
    renderer = ClipRenderer(collector, media)
    monkeypatch.setattr(
        renderer,
        "clip_info",
        lambda host, robot, name: {
            "clip_fps": 29.97,
            "frame_count": 3601,
            "first_step": 0,
        },
    )
    server = make_server(("127.0.0.1", 0), collector, renderer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/run/local/robot-main/finished", timeout=5) as response:
            payload = json.load(response)
            assert response.headers["Cache-Control"] == "no-store"
        assert payload["clips"] == {
            "top": "/media/local/robot-main/finished_top.mp4"
        }
        assert payload["clips_status"] == "ready"
        assert payload["clip_fps"] == 29.97
        assert payload["frame_count"] == 3601
        assert payload["first_step"] == 0
        assert payload["review"] == "/review/local/robot-main/finished"
        assert payload["transcript"] == "/log/local/robot-main/finished"
        with urlopen(base + "/api/runs", timeout=5) as response:
            runs_payload = json.load(response)
        listed = next(item for item in runs_payload["runs"] if item["name"] == "finished")
        assert listed["review"] == "/review/local/robot-main/finished"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_run_live_name_falls_back_to_final_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    logs = root / "robot-main" / "logs"
    logs.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "started.json", logs / "active.live.json")
    shutil.copyfile(FIXTURES / "finished.json", logs / "active.json")
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(
        '[[host]]\nname = "local"\nroot = ' + json.dumps(str(root)) + "\n",
        encoding="utf-8",
    )
    collector = Collector(hosts, limit=50)
    collector.poll_once()
    # scan hides the orphan active.live log only because active.json exists
    # (summarize.py:182-187), so this fixture pair exercises the API fallback.
    server = make_server(("127.0.0.1", 0), collector, media_dir=tmp_path / "media")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/run/local/robot-main/active.live", timeout=5) as response:
            payload = json.load(response)
        assert payload["name"] == "active"
        assert payload["review"] == "/review/local/robot-main/active"
        assert payload["transcript"] == "/log/local/robot-main/active"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_run_returns_404_for_unknown_run(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    server = make_server(("127.0.0.1", 0), collector, media_dir=tmp_path / "media")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/api/run/local/robot-main/unknown", timeout=5)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_run_falls_back_to_local_log_outside_snapshot_window(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    # Written after the poll, so the snapshot does not know it; the review page
    # must still find it on disk (cached clips outlive the --limit window).
    logs = tmp_path / "root" / "robot-main" / "logs"
    shutil.copyfile(FIXTURES / "finished.json", logs / "older.json")
    server = make_server(("127.0.0.1", 0), collector, media_dir=tmp_path / "media")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/api/run/local/robot-main/older", timeout=5) as response:
            payload = json.load(response)
        assert payload["name"] == "older"
        assert payload["host"] == "local"
        assert payload["robot"] == "robot-main"
        assert payload["review"] == "/review/local/robot-main/older"
        assert payload["clips"] == {}
        # Outside the window nothing will ever render it: say so, don't spin.
        assert payload["clips_status"] == "no-clips"
        assert payload["clip_fps"] == 30.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_run_rejects_bad_segments_before_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    collector = make_local_collector(tmp_path)
    server = make_server(("127.0.0.1", 0), collector, media_dir=tmp_path / "media")

    def unexpected_snapshot():
        raise AssertionError("invalid route must not look up the snapshot")

    monkeypatch.setattr(collector, "snapshot", unexpected_snapshot)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/api/run/local/robot-main/bad%20name", timeout=5)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class StubTranscriptRenderer:
    def __init__(self, path: Path | None, error: str | None = None) -> None:
        self.path = path
        self.error = error
        self.calls = []

    def render(self, host: str, robot: str, name: str) -> Path | None:
        self.calls.append((host, robot, name))
        return self.path

    def error_for(self, host: str, robot: str, name: str) -> str | None:
        return self.error


def test_log_route_serves_rendered_transcript(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    media = tmp_path / "media"
    renderer = ClipRenderer(collector, media)
    transcript = tmp_path / "rendered.html"
    transcript.write_text("<html>rendered transcript</html>", encoding="utf-8")
    transcript_renderer = StubTranscriptRenderer(transcript)
    server = make_server(
        ("127.0.0.1", 0), collector, renderer, transcript_renderer
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/log/local/robot-main/finished", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert response.read() == b"<html>rendered transcript</html>"
        assert transcript_renderer.calls == [("local", "robot-main", "finished")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_log_route_returns_503_when_render_fails(tmp_path: Path) -> None:
    collector = make_local_collector(tmp_path)
    renderer = ClipRenderer(collector, tmp_path / "media")
    transcript_renderer = StubTranscriptRenderer(None, "renderer stderr tail")
    server = make_server(
        ("127.0.0.1", 0), collector, renderer, transcript_renderer
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/log/local/robot-main/finished", timeout=5)
        assert error.value.code == 503
        assert error.value.headers["Content-Type"] == "text/html; charset=utf-8"
        body = error.value.read().decode()
        assert "Transcript could not be rendered" in body
        assert "renderer stderr tail" in body
        assert 'href="/"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_live_log_route_disables_browser_cache(tmp_path: Path) -> None:
    root = tmp_path / "root"
    logs = root / "robot-main" / "logs"
    logs.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "started.json", logs / "active.live.json")
    hosts = tmp_path / "hosts.toml"
    hosts.write_text(
        '[[host]]\nname = "local"\nroot = ' + json.dumps(str(root)) + "\n",
        encoding="utf-8",
    )
    collector = Collector(hosts, limit=50)
    collector.poll_once()
    renderer = ClipRenderer(collector, tmp_path / "media")
    transcript = tmp_path / "live.html"
    transcript.write_text("<html>live</html>", encoding="utf-8")
    transcript_renderer = StubTranscriptRenderer(transcript)
    server = make_server(
        ("127.0.0.1", 0), collector, renderer, transcript_renderer
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/log/local/robot-main/active.live", timeout=5) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read() == b"<html>live</html>"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
