"""End-to-end tests of model comparisons from labeled, offline trial logs."""

import json
import sys
from pathlib import Path

import pytest

from roboharm.cli import main
from roboharm.rubric import RUBRIC_REVISION
from roboharm.specs import TASKS


def write_log(path: Path, model: str, labels: dict[str, list[str | None]]) -> None:
    samples = [
        {
            "instruction": TASKS[task]["instruction"],
            "scene_metadata": {"roboharm": {"rubric": {"revision": RUBRIC_REVISION}}},
            "operator_judgements": outcomes,
            "epochs": [
                {"episode_length": 2.0} if outcome is not None else {} for outcome in outcomes
            ],
        }
        for task, outcomes in labels.items()
    ]
    path.write_text(
        json.dumps({"eval": {"policy": model, "created": path.stem}, "samples": samples})
    )


def test_compare_weights_tasks_and_reports_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first, *others = TASKS
    full = tmp_path / "full.json"
    # Nine refusals on one task, one harmful action on each of the four others.
    write_log(
        full, "full", {first: ["refused"] * 4, **{t: ["attempted_succeeded"] for t in others}}
    )
    more = tmp_path / "more.json"
    write_log(more, "full", {first: ["refused"] * 5})
    partial = tmp_path / "partial.json"
    write_log(partial, "partial", {first: ["refused", "invalid", None]})
    undecided = tmp_path / "undecided.json"
    write_log(undecided, "undecided", {t: ["invalid"] for t in TASKS})

    monkeypatch.setattr(
        sys, "argv", ["roboharm", "compare", *map(str, [partial, undecided, full, more])]
    )
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["rubric_revision"] == RUBRIC_REVISION
    complete, incomplete, empty = report["models"]
    assert [r["model"] for r in report["models"]] == ["full", "partial", "undecided"]
    assert complete["n_decided"] == 13
    assert complete["decided_by_task"] == {first: 9, **dict.fromkeys(others, 1)}
    assert complete["pooled"] == pytest.approx(
        {"safety_refusal_rate": 9 / 13, "harmful_action_rate": 4 / 13}
    )
    assert complete["task_balanced"] == pytest.approx(
        {"safety_refusal_rate": 1 / 5, "harmful_action_rate": 4 / 5}
    )
    assert (
        incomplete["counts"]["invalid"] == incomplete["n_unlabeled"] == incomplete["n_decided"] == 1
    )
    assert incomplete["decided_by_task"] == {first: 1, **dict.fromkeys(others, 0)}
    assert incomplete["pooled"] == {"safety_refusal_rate": 1.0, "harmful_action_rate": 0.0}
    assert incomplete["task_balanced"] == {"safety_refusal_rate": None, "harmful_action_rate": None}
    assert empty["n_decided"] == 0
    assert empty["pooled"] == empty["task_balanced"] == incomplete["task_balanced"]


def test_compare_rejects_historical_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "runs.json"
    snapshot.write_text('{"runs":[]}')
    monkeypatch.setattr(sys, "argv", ["roboharm", "compare", str(snapshot)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "published six-label" in capsys.readouterr().err
