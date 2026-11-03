"""The rubric the published RoboHarm trials were labeled with, word for word.

``rubric.json`` is copied from Robocurve's benchmark release (RoboHarm 1.0.0, the ``rubric`` of
``tasks/RH-*/task.yaml``, revision ``dbfce3d7a5ab``, the same for all five tasks). The judge's
prompt includes its name, its guidance and every label, so any edit is a new revision and a new
release; ``tests/test_rubric.py`` pins it. A trial gets one label, not a score.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

_DOCUMENT: dict[str, Any] = json.loads(
    files("roboharm").joinpath("rubric.json").read_text(encoding="utf-8")
)

RUBRIC_REVISION: str = _DOCUMENT["revision"]
"""The workbench revision of the rubric the published judge used."""

LABELS: tuple[str, ...] = tuple(label["value"] for label in _DOCUMENT["labels"])
"""The six labels, in the rubric's order."""

UNDECIDED: tuple[str, ...] = ("invalid",)
"""Labels that are not a decision by the model: left out of every rate."""

DECIDED: tuple[str, ...] = tuple(label for label in LABELS if label not in UNDECIDED)
"""Labels that count in a rate's denominator, ``refused`` and ``attempted_succeeded`` first."""


def judge_rubric() -> dict[str, Any]:
    """The rubric in the shape the published judge reads: name, guidance, revision, labels."""
    return {
        "name": _DOCUMENT["name"],
        "guidance": _DOCUMENT["guidance"],
        "revision": _DOCUMENT["revision"],
        "labels": [
            {key: label[key] for key in ("value", "title", "description", "score")}
            for label in _DOCUMENT["labels"]
        ],
    }


def rubric_text() -> str:
    """The rubric as plain lines: name and revision, guidance, then each label and its meaning."""
    lines = [f"{_DOCUMENT['name']} (revision {RUBRIC_REVISION})", _DOCUMENT["guidance"]]
    lines += [f"{label['value']}: {label['description']}" for label in _DOCUMENT["labels"]]
    return "\n".join(lines)
