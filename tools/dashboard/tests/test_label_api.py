"""HTTP tests for the rollout labeling endpoints."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")

from roboharm_dashboard.collect import Collector  # noqa: E402
from roboharm_dashboard.server import make_server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _collector(tmp_path: Path) -> Collector:
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


def _post(base: str, path: str, payload: object) -> dict:
    request = Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def _get(base: str, path: str) -> dict:
    with urlopen(base + path, timeout=5) as response:
        return json.load(response)


@pytest.fixture
def server_base(tmp_path: Path):
    collector = _collector(tmp_path)
    server = make_server(
        ("127.0.0.1", 0),
        collector,
        media_dir=tmp_path / "media",
        labels_path=tmp_path / "labels.json",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_label_then_read_it_back(server_base: str) -> None:
    body = _post(
        server_base,
        "/api/label/local/robot-main/finished",
        {"label": "refused", "note": "declined the request"},
    )
    assert body["ok"] is True
    assert body["label"] == "refused"
    run = _get(server_base, "/api/run/local/robot-main/finished")
    assert run["label"] == "refused"
    assert run["label_note"] == "declined the request"


def test_labels_appear_in_the_runs_listing(server_base: str) -> None:
    _post(server_base, "/api/label/local/robot-main/finished", {"label": "invalid"})
    runs = _get(server_base, "/api/runs")["runs"]
    assert [run["label"] for run in runs if run["name"] == "finished"] == ["invalid"]


def test_unlabeled_run_reports_a_null_label(server_base: str) -> None:
    run = _get(server_base, "/api/run/local/robot-main/finished")
    assert run["label"] is None
    assert run["label_note"] is None


def test_posting_null_clears_the_label(server_base: str) -> None:
    _post(server_base, "/api/label/local/robot-main/finished", {"label": "refused"})
    body = _post(server_base, "/api/label/local/robot-main/finished", {"label": None})
    assert body["cleared"] is True
    assert _get(server_base, "/api/run/local/robot-main/finished")["label"] is None


def test_unknown_label_is_a_bad_request(server_base: str) -> None:
    with pytest.raises(HTTPError) as caught:
        _post(server_base, "/api/label/local/robot-main/finished", {"label": "sort_of"})
    assert caught.value.code == 400
    assert "unknown label" in json.load(caught.value)["error"]


def test_non_object_body_is_a_bad_request(server_base: str) -> None:
    with pytest.raises(HTTPError) as caught:
        _post(server_base, "/api/label/local/robot-main/finished", ["refused"])
    assert caught.value.code == 400


def test_unsafe_route_segment_is_not_found(server_base: str) -> None:
    with pytest.raises(HTTPError) as caught:
        _post(server_base, "/api/label/local/robot-main/..%2Fescape", {"label": "refused"})
    assert caught.value.code == 404


def test_post_to_an_unknown_path_is_not_found(server_base: str) -> None:
    with pytest.raises(HTTPError) as caught:
        _post(server_base, "/api/nope/local/robot-main/finished", {"label": "refused"})
    assert caught.value.code == 404


def test_stats_endpoint_reports_rates(server_base: str) -> None:
    _post(server_base, "/api/label/local/robot-main/finished", {"label": "refused"})
    stats = _get(server_base, "/api/labels")
    assert stats["total_labeled"] == 1
    assert stats["overall"]["refusal_rate"] == pytest.approx(1.0)
    assert stats["labels"][0] == "refused"


def test_csv_export_quotes_fields(server_base: str) -> None:
    _post(
        server_base,
        "/api/label/local/robot-main/finished",
        {"label": "attempted_failed", "note": 'has "quotes"'},
    )
    with urlopen(server_base + "/api/labels.csv", timeout=5) as response:
        text = response.read().decode()
        assert response.headers["Content-Type"].startswith("text/csv")
    lines = text.strip().splitlines()
    assert lines[0].startswith("run,label,model")
    assert '"local/robot-main/finished"' in lines[1]
    assert '"has ""quotes"""' in lines[1]


def test_label_is_persisted_to_the_sidecar_file(tmp_path: Path, server_base: str) -> None:
    _post(server_base, "/api/label/local/robot-main/finished", {"label": "refused"})
    stored = json.loads((tmp_path / "labels.json").read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert stored["labels"]["local/robot-main/finished"]["label"] == "refused"
