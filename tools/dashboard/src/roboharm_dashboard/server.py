"""HTTP server for the robot dashboard."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .collect import Collector, encode_jpeg
from .summarize import summarize_log
from .clips import ClipRenderer
from .labels import LABELS, LabelStore
from .transcripts import TranscriptRenderer


_SAFE_ROUTE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_MEDIA_FILE = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")
_BYTE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        collector: Collector,
        renderer: ClipRenderer,
        transcript_renderer: TranscriptRenderer | None = None,
        label_store: LabelStore | None = None,
    ) -> None:
        self.collector = collector
        self.renderer = renderer
        self.labels = (
            label_store
            if label_store is not None
            else LabelStore(Path(renderer.media_dir) / "labels.json")
        )
        self.transcript_renderer = (
            transcript_renderer
            if transcript_renderer is not None
            else TranscriptRenderer(collector, renderer.media_dir)
        )
        self.index_html = (
            resources.files("roboharm_dashboard").joinpath("static", "index.html").read_bytes()
        )
        self.review_html = (
            resources.files("roboharm_dashboard").joinpath("static", "review.html").read_bytes()
        )
        self.jpeg_cache: OrderedDict[tuple[str, str, str, str, int], bytes] = OrderedDict()
        self.jpeg_cache_lock = threading.Lock()
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stderr useful: report only errors, not every two-second poll.
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        no_store: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _not_found(self, message: str = "Not found") -> None:
        self._send(404, (message + "\n").encode(), "text/plain; charset=utf-8")

    @staticmethod
    def _valid_run_key(host: str, robot: str, name: str) -> bool:
        return all(_SAFE_ROUTE_SEGMENT.fullmatch(value) for value in (host, robot, name))

    def _enrich_run(self, run: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(run)
        encoded_key = "/".join(
            quote(str(enriched.get(field, "")), safe="")
            for field in ("host", "robot", "name")
        )
        enriched["transcript"] = "/log/" + encoded_key
        enriched["review"] = "/review/" + encoded_key
        try:
            key = (enriched["host"], enriched["robot"], enriched["name"])
            enriched["clips"] = self.server.renderer.clips_for(*key)
            enriched["clips_status"] = self.server.renderer.status_for(*key)
        except (KeyError, TypeError, ValueError):
            enriched["clips"] = {}
            enriched["clips_status"] = "no-clips"
        try:
            entry = self.server.labels.get(
                str(enriched.get("host", "")),
                str(enriched.get("robot", "")),
                str(enriched.get("name", "")),
            )
        except (OSError, ValueError):
            entry = None
        enriched["label"] = entry.get("label") if entry else None
        enriched["label_note"] = entry.get("note") if entry else None
        return enriched

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send(200, self.server.index_html, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/labels":
            body = json.dumps(self.server.labels.stats()).encode()
            self._send(200, body, "application/json; charset=utf-8", no_store=True)
            return
        if parsed.path == "/api/labels.csv":
            self._send(
                200,
                _labels_csv(self.server.labels.all()),
                "text/csv; charset=utf-8",
                no_store=True,
            )
            return
        if parsed.path == "/api/runs":
            snapshot = self.server.collector.snapshot()
            snapshot["runs"] = [self._enrich_run(run) for run in snapshot["runs"]]
            body = json.dumps(snapshot).encode()
            self._send(200, body, "application/json; charset=utf-8", no_store=True)
            return

        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[0] == "review":
            if not self._valid_run_key(parts[1], parts[2], parts[3]):
                self._not_found()
                return
            self._send(200, self.server.review_html, "text/html; charset=utf-8")
            return
        if len(parts) == 5 and parts[:2] == ["api", "run"]:
            self._serve_api_run(parts[2], parts[3], parts[4])
            return
        if len(parts) == 4 and parts[0] == "thumb":
            self._serve_thumb(parts[1], parts[2], parts[3], parsed.query)
            return
        if len(parts) == 4 and parts[0] == "log":
            self._serve_log(parts[1], parts[2], parts[3])
            return
        if len(parts) == 4 and parts[0] == "media":
            self._serve_media(parts[1], parts[2], parts[3])
            return
        self._not_found()

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        """Record or clear one rollout label.

        The only write endpoint in the dashboard. Labels are the researcher's
        own hand judgements, so they go to the sidecar store and never touch
        the immutable EvalLog the run wrote.
        """

        parsed = urlsplit(self.path)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) != 5 or parts[:2] != ["api", "label"]:
            self._not_found()
            return
        host, robot, name = parts[2], parts[3], parts[4]
        if not self._valid_run_key(host, robot, name):
            self._not_found()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._bad_request("Content-Length must be an integer")
            return
        if length < 0 or length > 64_000:
            self._bad_request("Request body too large")
            return
        try:
            raw = self.rfile.read(length) if length else b"{}"
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, UnicodeError):
            self._bad_request("Body must be JSON")
            return
        if not isinstance(payload, dict):
            self._bad_request("Body must be a JSON object")
            return

        if payload.get("label") is None:
            cleared = self.server.labels.clear(host, robot, name)
            body = json.dumps({"ok": True, "label": None, "cleared": cleared}).encode()
            self._send(200, body, "application/json; charset=utf-8", no_store=True)
            return

        label = payload.get("label")
        if not isinstance(label, str):
            self._bad_request("label must be a string")
            return
        note = payload.get("note")
        if note is not None and not isinstance(note, str):
            self._bad_request("note must be a string")
            return
        run = self._run_for_label(host, robot, name)
        try:
            entry = self.server.labels.set(host, robot, name, label, note=note, run=run)
        except ValueError as exc:
            self._bad_request(str(exc))
            return
        except OSError as exc:
            self._send(
                500,
                json.dumps({"ok": False, "error": f"could not save label: {exc}"}).encode(),
                "application/json; charset=utf-8",
                no_store=True,
            )
            return
        body = json.dumps({"ok": True, **entry}).encode()
        self._send(200, body, "application/json; charset=utf-8", no_store=True)

    def _bad_request(self, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}).encode()
        self._send(400, body, "application/json; charset=utf-8", no_store=True)

    def _run_for_label(self, host: str, robot: str, name: str) -> dict[str, Any] | None:
        """Best-effort run facts to denormalize next to a label."""

        try:
            snapshot = self.server.collector.snapshot()
            runs = snapshot.get("runs", [])
        except (OSError, ValueError, KeyError):
            return None
        if not isinstance(runs, list):
            return None
        for candidate in (name, name[:-5] if name.endswith(".live") else name):
            for run in runs:
                if (
                    isinstance(run, dict)
                    and run.get("host") == host
                    and run.get("robot") == robot
                    and run.get("name") == candidate
                ):
                    return run
        return self._run_from_disk(host, robot, name)

    def _run_from_disk(self, host: str, robot: str, name: str) -> dict[str, Any] | None:
        """Summarize a local run that fell outside the collector's recent window.

        The snapshot keeps only the newest ``--limit`` logs per robot, but cached
        clips (and the logs themselves) outlive that window. Segments are
        already validated by the caller; remote hosts are not read here.
        """

        host_config = self.server.collector.host(host)
        if host_config is None or host_config.ssh is not None:
            return None
        candidates = [name]
        if name.endswith(".live"):
            candidates.insert(0, name[:-5])
        for candidate in candidates:
            log_path = Path(host_config.root) / robot / "logs" / f"{candidate}.json"
            if not log_path.is_file():
                continue
            try:
                run = summarize_log(log_path)
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                return None
            run["host"] = host
            return run
        return None

    def _serve_api_run(self, host: str, robot: str, name: str) -> None:
        if not self._valid_run_key(host, robot, name):
            self._not_found()
            return
        snapshot = self.server.collector.snapshot()
        runs = snapshot.get("runs", [])
        if not isinstance(runs, list):
            self._not_found()
            return

        def match(run_name: str) -> dict[str, Any] | None:
            return next(
                (
                    run
                    for run in runs
                    if isinstance(run, dict)
                    and run.get("host") == host
                    and run.get("robot") == robot
                    and run.get("name") == run_name
                ),
                None,
            )

        run = match(name)
        if run is None and name.endswith(".live"):
            run = match(name[:-5])
        from_disk = False
        if run is None:
            run = self._run_from_disk(host, robot, name)
            from_disk = run is not None
        if run is None:
            self._not_found("Run not found")
            return

        enriched = self._enrich_run(run)
        if from_disk and not enriched.get("clips"):
            # The renderer only works the collector snapshot, so a run that
            # fell out of the window will never gain clips: say so instead of
            # letting the page wait on "rendering".
            enriched["clips_status"] = "no-clips"
        try:
            enriched.update(
                self.server.renderer.clip_info(
                    enriched["host"], enriched["robot"], enriched["name"]
                )
            )
        except (KeyError, TypeError, ValueError):
            enriched.update(
                {"clip_fps": 30.0, "frame_count": None, "first_step": 0}
            )
        body = json.dumps(enriched).encode()
        self._send(200, body, "application/json; charset=utf-8", no_store=True)

    def _serve_thumb(self, host: str, robot: str, name: str, query: str) -> None:
        cam = parse_qs(query).get("cam", ["top_cam"])[0]
        if not all(_SAFE_ROUTE_SEGMENT.fullmatch(value) for value in (host, robot, name)):
            self._not_found()
            return
        snapshot = self.server.collector.snapshot()
        run = next(
            (
                item
                for item in snapshot["runs"]
                if item.get("host") == host
                and item.get("robot") == robot
                and item.get("name") == name
            ),
            None,
        )
        frames_dir = run.get("frames_dir") if run else None
        if not isinstance(frames_dir, str) or not frames_dir:
            self._not_found("No camera frames are available for this run")
            return
        try:
            latest = self.server.collector.latest_frame(host, robot, frames_dir, cam)
        except (OSError, ValueError, RuntimeError):
            latest = None
        except Exception:
            # Subprocess and image transport errors should degrade to a placeholder.
            latest = None
        if latest is None:
            self._not_found("No camera frames are available for this run")
            return

        index, npy_bytes = latest
        cache_key = (host, robot, name, cam, index)
        with self.server.jpeg_cache_lock:
            jpeg = self.server.jpeg_cache.get(cache_key)
            if jpeg is not None:
                self.server.jpeg_cache.move_to_end(cache_key)
        if jpeg is None:
            try:
                jpeg = encode_jpeg(npy_bytes)
            except Exception:
                self._not_found("The latest camera frame could not be decoded")
                return
            with self.server.jpeg_cache_lock:
                self.server.jpeg_cache[cache_key] = jpeg
                self.server.jpeg_cache.move_to_end(cache_key)
                while len(self.server.jpeg_cache) > 128:
                    self.server.jpeg_cache.popitem(last=False)
        self._send(200, jpeg, "image/jpeg", no_store=True)

    def _serve_log(self, host: str, robot: str, name: str) -> None:
        reason = "The run log is unavailable or inspect-robots view failed."
        try:
            path = self.server.transcript_renderer.render(host, robot, name)
        except ValueError as exc:
            reason = str(exc)
            path = None
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            path = None
        if path is None:
            try:
                render_reason = self.server.transcript_renderer.error_for(host, robot, name)
            except (AttributeError, ValueError):
                render_reason = None
            self._serve_transcript_error(render_reason or reason)
            return
        try:
            body = path.read_bytes()
        except OSError as exc:
            self._serve_transcript_error(str(exc) or "Rendered transcript disappeared")
            return

        snapshot = self.server.collector.snapshot()
        run = next(
            (
                item
                for item in snapshot.get("runs", [])
                if item.get("host") == host
                and item.get("robot") == robot
                and item.get("name") == name
            ),
            None,
        )
        live = bool(
            name.endswith(".live")
            or (run and (run.get("live") or run.get("status") == "started"))
        )
        self._send(200, body, "text/html; charset=utf-8", no_store=live)

    def _serve_transcript_error(self, reason: str) -> None:
        escaped_reason = html.escape(reason)
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transcript unavailable</title><style>
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0a0d12; color: #edf2f8; font: 18px/1.5 system-ui, sans-serif; }}
main {{ width: min(620px, calc(100% - 40px)); padding: 28px; border: 1px solid #2a3546; border-radius: 14px; background: #131922; }}
h1 {{ margin-top: 0; }} p {{ color: #b7c1cf; overflow-wrap: anywhere; }} a {{ color: #80bdff; }}
</style></head><body><main><h1>Transcript could not be rendered</h1><p>{escaped_reason}</p><a href="/">Back to dashboard</a></main></body></html>"""
        self._send(503, body.encode(), "text/html; charset=utf-8", no_store=True)

    def _serve_media(self, host: str, robot: str, filename: str) -> None:
        if (
            not _SAFE_ROUTE_SEGMENT.fullmatch(host)
            or not _SAFE_ROUTE_SEGMENT.fullmatch(robot)
            or not _SAFE_MEDIA_FILE.fullmatch(filename)
        ):
            self._not_found()
            return
        path = self.server.renderer.media_dir / host / robot / filename
        try:
            size = path.stat().st_size
        except OSError:
            self._not_found()
            return
        if not path.is_file():
            self._not_found()
            return

        range_header = self.headers.get("Range")
        if range_header is None:
            start, end, status = 0, size - 1, 200
        else:
            parsed_range = self._parse_range(range_header, size)
            if parsed_range is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed_range
            status = 206

        length = max(0, end - start + 1)
        try:
            with path.open("rb") as file:
                file.seek(start)
                body = file.read(length)
        except OSError:
            self._not_found("Media file is no longer available")
            return
        if len(body) != length:
            self._not_found("Media file is no longer available")
            return

        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _parse_range(header: str, size: int) -> tuple[int, int] | None:
        match = _BYTE_RANGE.fullmatch(header.strip())
        if match is None or size <= 0:
            return None
        first, last = match.groups()
        if not first and not last:
            return None
        if first:
            start = int(first)
            if start >= size:
                return None
            end = min(int(last), size - 1) if last else size - 1
            if end < start:
                return None
            return start, end
        suffix_length = int(last)
        if suffix_length <= 0:
            return None
        return max(0, size - suffix_length), size - 1


def _labels_csv(entries: dict[str, Any]) -> bytes:
    """Render the label sidecar as CSV for analysis outside the dashboard."""

    columns = ("run", "label", "model", "policy", "task", "instruction", "status", "note")
    lines = [",".join(columns)]
    for run_key in sorted(entries):
        entry = entries[run_key]
        if not isinstance(entry, dict) or entry.get("label") not in LABELS:
            continue
        row = [run_key, *(str(entry.get(field, "")) for field in columns[1:])]
        lines.append(",".join('"' + field.replace('"', '""') + '"' for field in row))
    return ("\n".join(lines) + "\n").encode()


def make_server(
    address: tuple[str, int],
    collector: Collector,
    renderer: ClipRenderer | None = None,
    transcript_renderer: TranscriptRenderer | None = None,
    *,
    media_dir: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> DashboardServer:
    """Construct a server (also useful to tests embedding it on port zero)."""

    clip_renderer = renderer if renderer is not None else ClipRenderer(collector, media_dir)
    transcript_cache = media_dir if media_dir is not None else clip_renderer.media_dir
    label_file = (
        Path(labels_path)
        if labels_path is not None
        else Path(transcript_cache) / "labels.json"
    )
    return DashboardServer(
        address,
        collector,
        clip_renderer,
        transcript_renderer
        if transcript_renderer is not None
        else TranscriptRenderer(collector, transcript_cache),
        LabelStore(label_file),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor inspect-robots robot runs")
    parser.add_argument("--hosts", default="hosts.toml", help="host configuration TOML")
    parser.add_argument("--port", type=int, default=8301)
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="recent logs per robot to scan; 0 (the default) scans every log",
    )
    parser.add_argument(
        "--media-dir",
        default=None,
        help="clip and transcript cache directory (default: media/ at the repository root)",
    )
    parser.add_argument(
        "--labels-path",
        default=None,
        help="rollout label sidecar (default: labels.json in the media directory)",
    )
    args = parser.parse_args()

    collector = Collector(Path(args.hosts), limit=args.limit)
    renderer = ClipRenderer(collector, args.media_dir)
    transcript_renderer = TranscriptRenderer(collector, renderer.media_dir)
    server = make_server(
        (args.host, args.port),
        collector,
        renderer,
        transcript_renderer,
        labels_path=args.labels_path,
    )
    collector.start()
    renderer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        renderer.stop()
        collector.stop()


if __name__ == "__main__":
    main()
