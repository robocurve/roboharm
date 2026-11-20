"""Independent trial review and nominal agreement under the published RoboHarm rubric."""

import csv
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, TextIO

from roboharm.labels import PROTOCOL
from roboharm.rubric import LABELS, RUBRIC_REVISION
from roboharm.runlogs import published_samples, recorded_trials


class TrialKey(NamedTuple):
    """The run's recorded identity plus its scene and one-based epoch."""

    run_task: str
    created: str
    seed: str
    scene_id: str
    epoch: int


_FIELDS = ("protocol", "rubric_revision", *TrialKey._fields, "log", "task", "label")


def _documents(paths: list[Path]) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict) or not isinstance(document.get("samples"), list):
            raise ValueError(f"{path}: expected an Inspect Robots run log")
        yield str(path), document


def _identity_field(value: object, where: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: missing {field} in trial identity")
    return value


def write_review_sheet(paths: list[Path], out: TextIO, *, blank: bool) -> dict[str, int]:
    """Write every eligible trial, including trials with missing or unrecognized labels."""
    rows = []
    seen: set[TrialKey] = set()
    skipped = {"live": 0, "other_instruction": 0, "other_rubric": 0}
    for name, spec, task, sample in published_samples(_documents(paths), skipped):
        run_task = _identity_field(spec.get("task"), name, "task")
        created = _identity_field(spec.get("created"), name, "created")
        scene = _identity_field(sample.get("scene_id"), name, "scene_id")
        seed = "" if spec.get("seed") is None else str(spec["seed"])
        for epoch, (label, _) in enumerate(recorded_trials(sample), start=1):
            key = TrialKey(run_task, created, seed, scene, epoch)
            if key in seen:
                raise ValueError(f"{name}: duplicate trial {key}")
            seen.add(key)
            rows.append((PROTOCOL, RUBRIC_REVISION, *key, name, task, "" if blank else label))
    writer = csv.writer(out)
    writer.writerow(_FIELDS)
    writer.writerows(rows)
    return skipped


def _load_sheet(path: Path) -> dict[TrialKey, str]:
    trials = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        header = reader.fieldnames or []
        required = {"protocol", "rubric_revision", *TrialKey._fields, "label"}
        if not required.issubset(header) or len(set(header)) != len(header):
            raise ValueError(f"{path}: expected review-sheet columns with unique names")
        for row in reader:
            where = f"{path} line {reader.line_num}"
            if None in row or None in row.values():
                raise ValueError(f"{where}: wrong number of CSV fields")
            if row["protocol"] != PROTOCOL or row["rubric_revision"] != RUBRIC_REVISION:
                raise ValueError(f"{where}: incompatible protocol or rubric revision")
            if not row["epoch"].isascii() or not row["epoch"].isdecimal() or int(row["epoch"]) < 1:
                raise ValueError(f"{where}: epoch must be a positive integer")
            key = TrialKey(
                _identity_field(row["run_task"], where, "run_task"),
                _identity_field(row["created"], where, "created"),
                row["seed"],
                _identity_field(row["scene_id"], where, "scene_id"),
                int(row["epoch"]),
            )
            if key in trials:
                raise ValueError(f"{where}: duplicate trial {key}")
            label = row["label"]
            if label and label not in LABELS:
                raise ValueError(f"{where}: unknown label {label!r}")
            trials[key] = label
    return trials


def agreement(left_path: Path, right_path: Path) -> dict[str, Any]:
    """Compare exact six-class verdicts only for trials both reviewers labeled."""
    left, right = _load_sheet(left_path), _load_sheet(right_path)
    shared = left.keys() & right.keys()
    confusion = {a: dict.fromkeys(LABELS, 0) for a in LABELS}
    left_counts: Counter[str] = Counter()
    right_counts: Counter[str] = Counter()
    disagreements = []
    for key in sorted(shared):
        a, b = left[key], right[key]
        if not a or not b:
            continue
        confusion[a][b] += 1
        left_counts[a] += 1
        right_counts[b] += 1
        if a != b:
            disagreements.append({**key._asdict(), "left": a, "right": b})
    paired = left_counts.total()
    agreed = sum(confusion[label][label] for label in LABELS)
    # Keep both terms integral until division. Kappa is undefined if chance agreement is one.
    expected_pairs = sum(left_counts[label] * right_counts[label] for label in LABELS)
    denominator = paired * paired - expected_pairs
    return {
        "protocol": PROTOCOL,
        "rubric_revision": RUBRIC_REVISION,
        "left_only": len(left) - len(shared),
        "right_only": len(right) - len(shared),
        "shared_unlabeled": len(shared) - paired,
        "paired": paired,
        "agreed": agreed,
        "observed_agreement": agreed / paired if paired else None,
        "cohens_kappa": (paired * agreed - expected_pairs) / denominator if denominator else None,
        "confusion": confusion,
        "disagreements": disagreements,
    }
