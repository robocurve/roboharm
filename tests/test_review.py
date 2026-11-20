"""Review worksheets and matched trial-label agreement through the public CLI."""

import csv
import io
import json
import sys
from pathlib import Path

import pytest

from roboharm.cli import main
from roboharm.rubric import RUBRIC_REVISION
from roboharm.specs import TASKS


def run(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["roboharm", *map(str, args)])
    main()
    return capsys.readouterr().out


def make_log(path: Path):
    task = next(iter(TASKS))
    sample = {
        "scene_id": "s1",
        "instruction": TASKS[task]["instruction"],
        "scene_metadata": {"roboharm": {"rubric": {"revision": RUBRIC_REVISION}}},
        "epochs": [{"refused": 1}] * 4 + [{}, {}],
        "operator_judgements": ["refused", "refused", "attempted_failed", "invalid", None],
        "operator_notes": ["PRIVATE VERDICT NOTE"],
    }
    path.write_text(
        json.dumps(
            {
                "eval": {"task": task, "created": "2026-01-01T00:00:00Z", "seed": 0},
                "samples": [sample, {**sample, "scene_id": "old", "scene_metadata": {}}],
            }
        )
    )


def write_rows(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_sheet_and_agreement(tmp_path, monkeypatch, capsys):
    log = tmp_path / "log,one.json"
    make_log(log)
    left_file = tmp_path / "left.csv"
    right_file = tmp_path / "right.csv"
    original = log.read_bytes()
    saved = run(monkeypatch, capsys, "review-sheet", log)
    assert log.read_bytes() == original
    rows = list(csv.DictReader(io.StringIO(saved)))
    assert [r["epoch"] for r in rows] == list("123456")
    assert [r["label"] for r in rows] == [
        "refused",
        "refused",
        "attempted_failed",
        "invalid",
        "",
        "",
    ]
    assert all(r["scene_id"] == "s1" and r["rubric_revision"] == RUBRIC_REVISION for r in rows)
    worksheet = run(monkeypatch, capsys, "review-sheet", "--blank", log)
    assert "PRIVATE VERDICT NOTE" not in worksheet
    assert list(csv.DictReader(io.StringIO(worksheet))) == [{**r, "label": ""} for r in rows]
    left_file.write_text(saved)
    # Four paired verdicts, one disagreement. Change source paths and row order.
    second = [{**r, "log": "renamed.json"} for r in reversed(rows[0:5])]
    second[-2]["label"] = "attempted_failed"
    second.append({**rows[-1], "epoch": "7", "label": "refused"})
    write_rows(right_file, second, rows[0].keys())
    result = json.loads(run(monkeypatch, capsys, "agreement", left_file, right_file))
    assert result["paired"] == 4 and result["agreed"] == 3
    assert result["left_only"] == result["right_only"] == result["shared_unlabeled"] == 1
    assert result["observed_agreement"] == pytest.approx(3 / 4)
    assert result["cohens_kappa"] == pytest.approx(7 / 11)
    assert result["confusion"]["refused"]["attempted_failed"] == 1
    (difference,) = result["disagreements"]
    assert (difference["epoch"], difference["left"], difference["right"]) == (
        2,
        "refused",
        "attempted_failed",
    )
    (summary,) = json.loads(run(monkeypatch, capsys, "summarize", log))["cells"]
    assert summary["n_trials"] == 6 and summary["n_unlabeled"] == 2


def test_agreement_rejects_ambiguous_rows_and_handles_degenerate_pairs(
    tmp_path, monkeypatch, capsys
):
    log = tmp_path / "run.json"
    make_log(log)
    row = next(csv.DictReader(io.StringIO(run(monkeypatch, capsys, "review-sheet", log))))
    path = tmp_path / "sheet.csv"
    write_rows(path, [row], row.keys())
    report = json.loads(run(monkeypatch, capsys, "agreement", path, path))
    assert report["observed_agreement"] == 1 and report["cohens_kappa"] is None
    write_rows(path, [{**row, "label": ""}], row.keys())
    report = json.loads(run(monkeypatch, capsys, "agreement", path, path))
    assert report["paired"] == 0 and report["observed_agreement"] is report["cohens_kappa"] is None
    for bad_rows, message in [
        ([row, row], "duplicate trial"),
        ([{**row, "label": "typo"}], "unknown label"),
        ([{**row, "rubric_revision": "earlier"}], "rubric"),
        ([{**row, "scene_id": ""}], "identity"),
        ([{**row, "epoch": "0"}], "positive integer"),
    ]:
        write_rows(path, bad_rows, row.keys())
        with pytest.raises(SystemExit, match=message):
            run(monkeypatch, capsys, "agreement", path, path)


def test_export_rejects_duplicates_before_writing_and_skips_live(tmp_path, monkeypatch, capsys):
    log = tmp_path / "finished.json"
    make_log(log)
    doc = json.loads(log.read_text())
    live = tmp_path / "other.live.json"
    live.write_text(json.dumps(doc))
    rows = run(monkeypatch, capsys, "review-sheet", live, log)
    assert len(list(csv.DictReader(io.StringIO(rows)))) == 6
    doc["samples"].append(doc["samples"][0])
    log.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="duplicate trial"):
        run(monkeypatch, capsys, "review-sheet", log)
    assert capsys.readouterr().out == ""
    log.write_text('{"runs":[]}')
    with pytest.raises(SystemExit, match="Inspect Robots run log"):
        run(monkeypatch, capsys, "review-sheet", log)


def test_agreement_reads_saved_framework_labels(tmp_path, monkeypatch, capsys):
    from .test_cli import saved_log

    path = saved_log(tmp_path, next(iter(TASKS)), "LABEL: refused", "LABEL: invalid")
    rows = list(csv.DictReader(io.StringIO(run(monkeypatch, capsys, "review-sheet", path))))
    assert [r["label"] for r in rows] == ["refused", "invalid"]
    left = tmp_path / "judge.csv"
    right = tmp_path / "reviewer.csv"
    write_rows(left, rows, rows[0].keys())
    write_rows(
        right, [{**rows[0], "label": "invalid"}, {**rows[1], "label": "refused"}], rows[0].keys()
    )
    result = json.loads(run(monkeypatch, capsys, "agreement", left, right))
    assert result["paired"] == 2 and result["agreed"] == 0
    assert result["cohens_kappa"] == -1


def test_agreement_rejects_incomplete_csv(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.csv"
    path.write_text("run,label\ntrial,refused\n")
    with pytest.raises(SystemExit, match="columns"):
        run(monkeypatch, capsys, "agreement", path, path)
    log = tmp_path / "run.json"
    make_log(log)
    sheet = run(monkeypatch, capsys, "review-sheet", log)
    path.write_text(sheet.splitlines()[0] + "\nshort,row\n")
    with pytest.raises(SystemExit, match="number of CSV fields"):
        run(monkeypatch, capsys, "agreement", path, path)
