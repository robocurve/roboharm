"""Offline command planning and label analysis of saved logs or a dashboard snapshot."""

import argparse
import csv
import json
import shlex
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from roboharm.commands import run_args
from roboharm.labels import (
    HISTORICAL_LABELS,
    HISTORICAL_PROTOCOL,
    PROTOCOL,
    RATE_NAMES,
    summarize,
    summarize_historical,
)
from roboharm.rubric import DECIDED, LABELS, RUBRIC_REVISION
from roboharm.specs import TASKS


def _model_name(model: str | None) -> str:
    # The dashboard may strip provider prefixes; use a canonical final component.
    return (model or "unknown").rsplit("/", 1)[-1]


def analyze(document: dict[str, Any]) -> dict[str, Any]:
    """Group a dashboard snapshot's finished, canonical runs under the historical five labels.

    The bundled dashboard records the original collector's five labels, so its snapshots keep the
    historical analysis; they are not remapped to the published rubric.
    """
    instructions = {s["instruction"]: key for key, s in TASKS.items()}
    groups = defaultdict(list)
    seen = set()
    skipped = {"live": 0, "other_instruction": 0, "unlabeled": 0}
    for run in document["runs"]:
        if run.get("live") or run["name"].endswith(".live"):
            skipped["live"] += 1
            continue
        identity = (run["host"], run["robot"], run["name"])
        if identity in seen:
            raise ValueError(f"Duplicate run identity: {identity}")
        seen.add(identity)
        task = instructions.get(run.get("instruction"))
        if task is None:
            skipped["other_instruction"] += 1
            continue
        label = run.get("label")
        if label is None:
            skipped["unlabeled"] += 1
        elif label not in HISTORICAL_LABELS:
            raise ValueError(f"Unknown label for {identity}: {label}")
        model = _model_name(run.get("model") or run.get("policy"))
        groups[(task, model)].append(run)
    rows = []
    for (task, model), runs in sorted(groups.items()):
        labels = [r["label"] for r in runs if r.get("label") is not None]
        rows.append(
            {
                "task": task,
                "model": model,
                "n_finished": len(runs),
                "n_unlabeled": len(runs) - len(labels),
                "robots": sorted({f"{r['host']}/{r['robot']}" for r in runs}),
                **summarize_historical(labels),
            }
        )
    return {"protocol": HISTORICAL_PROTOCOL, "skipped": skipped, "cells": rows}


def analyze_logs(logs: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Count each task and model's labels in saved Inspect Robots logs, with rates.

    A trial's label is its recorded judgement (``samples[].operator_judgements``), exactly as the
    ``roboharm_astra`` and ``roboharm_operator`` graders write it. Errored trials (never graded by
    Inspect Robots) and judgements that are not a label count as unlabeled, never as a label.
    Only scenes built by a task with this rubric revision count: logs of RoboHarm 0.1.0 tasks or
    of ad-hoc ``--instruction`` runs carry no rubric (or another one) and are skipped, not
    remapped. A log given twice (the same run) is refused rather than counted twice.
    """
    instructions = {s["instruction"]: key for key, s in TASKS.items()}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    skipped = {"live": 0, "other_instruction": 0, "other_rubric": 0}
    seen: dict[tuple[Any, ...], str] = {}
    for name, document in logs:
        if name.endswith(".live.json") or document.get("status") == "started":
            skipped["live"] += 1
            continue
        spec = document.get("eval") or {}
        identity = (spec.get("task"), spec.get("created"), spec.get("seed"))
        if identity in seen:
            raise ValueError(f"Duplicate log: {name} is the same run as {seen[identity]}")
        seen[identity] = name
        model = _model_name((spec.get("policy_config") or {}).get("model") or spec.get("policy"))
        for sample in document.get("samples") or []:
            task = instructions.get(sample.get("instruction"))
            if task is None:
                skipped["other_instruction"] += 1
                continue
            scene = sample.get("scene_metadata") or {}
            if (scene.get("roboharm") or {}).get("rubric", {}).get("revision") != RUBRIC_REVISION:
                skipped["other_rubric"] += 1
                continue
            cell = groups.setdefault(
                (task, model),
                {"logs": set(), "labels": [], "n_trials": 0, "n_errored": 0, "n_unlabeled": 0},
            )
            cell["logs"].add(name)
            judgements = sample.get("operator_judgements") or []
            epochs = sample.get("epochs") or []
            cell["n_trials"] += len(judgements)
            cell["n_errored"] += sum(1 for values in epochs if values == {})
            for judgement in judgements:
                if judgement in LABELS:
                    cell["labels"].append(judgement)
                else:
                    cell["n_unlabeled"] += 1
    rows = []
    for (task, model), cell in sorted(groups.items()):
        rows.append(
            {
                "task": task,
                "model": model,
                "n_logs": len(cell["logs"]),
                "n_trials": cell["n_trials"],
                "n_errored": cell["n_errored"],
                "n_unlabeled": cell["n_unlabeled"],
                **summarize(cell["labels"]),
            }
        )
    return {
        "protocol": PROTOCOL,
        "rubric_revision": RUBRIC_REVISION,
        "skipped": skipped,
        "cells": rows,
    }


def _load(paths: list[Path]) -> dict[str, Any]:
    documents = [(str(path), json.loads(path.read_text())) for path in paths]
    try:
        return _analyze(documents)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


def _analyze(documents: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    snapshots = [doc for _, doc in documents if "runs" in doc]
    if snapshots:
        if len(documents) != 1:
            raise SystemExit("summarize takes one dashboard snapshot, or any number of run logs")
        return analyze(snapshots[0])
    for name, doc in documents:
        if "samples" not in doc:
            raise SystemExit(f"{name}: not an Inspect Robots log or a dashboard /api/runs export")
    return analyze_logs(documents)


HISTORICAL_FIELDS = [
    "task",
    "model",
    "n_finished",
    "n_labeled",
    "n_unlabeled",
    "n_valid",
    "n_attempted",
    "safety_refusal_rate",
    "capability_refusal_rate",
    "attempt_rate",
    "completion_rate",
    "completion_given_attempt",
]

FIELDS = [
    "task",
    "model",
    "n_logs",
    "n_trials",
    "n_errored",
    "n_unlabeled",
    "n_labeled",
    "n_decided",
    *[f"n_{label}" for label in LABELS],
    *[RATE_NAMES[label] for label in DECIDED],
    "attempt_rate",
    "harmful_given_attempt",
]


def main() -> None:
    """Print commands by default; never move a robot from the planner."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks", help="print canonical task instructions")
    plan = sub.add_parser("command", help="print a single-rollout ./run command")
    plan.add_argument("task", choices=TASKS)
    plan.add_argument("--model", choices=["astra", "fable", "molmoact2"], required=True)
    plan.add_argument("--server-url", default="http://127.0.0.1:8202")
    stats = sub.add_parser(
        "summarize",
        help="count labels in Inspect Robots logs, or analyze a dashboard /api/runs export",
    )
    stats.add_argument("paths", type=Path, nargs="+", metavar="log_or_snapshot")
    stats.add_argument("--csv", action="store_true")
    args = parser.parse_args()
    if args.command == "tasks":
        for key, spec in TASKS.items():
            print(f"{key}\t{spec['instruction']}")
    elif args.command == "command":
        print(shlex.join(["./run", *run_args(args.task, args.model, args.server_url)]))
    else:
        result = _load(args.paths)
        errored = sum(cell.get("n_errored", 0) for cell in result["cells"])
        if errored:
            print(
                f"note: {errored} trial(s) ended in an error and were never graded "
                "(Inspect Robots grades only finished trials); label model errors and provider "
                "blocks by hand",
                file=sys.stderr,
            )
        if args.csv:
            historical = result["protocol"] == HISTORICAL_PROTOCOL
            fields = HISTORICAL_FIELDS if historical else FIELDS
            writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for cell in result["cells"]:
                counts = {f"n_{label}": n for label, n in cell["counts"].items()}
                writer.writerow({**cell, **({} if historical else counts)})
        else:
            print(json.dumps(result, indent=2))
