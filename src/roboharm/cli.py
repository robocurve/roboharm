"""Offline command planning and analysis of an exported dashboard snapshot."""

import argparse
import csv
import json
import shlex
from collections import defaultdict
from pathlib import Path

from roboharm.commands import run_args
from roboharm.labels import LABELS, summarize
from roboharm.specs import TASKS


def analyze(document: dict) -> dict:
    """Group finished, canonical runs without conflating status with hand labels."""
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
        elif label not in LABELS:
            raise ValueError(f"Unknown label for {identity}: {label}")
        model = run.get("model") or run.get("policy") or "unknown"
        # The dashboard may strip provider prefixes; use a canonical final component.
        model = model.rsplit("/", 1)[-1]
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
                **summarize(labels),
            }
        )
    return {"protocol": "historical-five-class-v1", "skipped": skipped, "cells": rows}


def main() -> None:
    """Print commands by default; never move a robot from the planner."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks", help="print canonical task instructions")
    plan = sub.add_parser("command", help="print a single-rollout ./run command")
    plan.add_argument("task", choices=TASKS)
    plan.add_argument("--model", choices=["astra", "fable", "molmoact2"], required=True)
    plan.add_argument("--server-url", default="http://127.0.0.1:8202")
    stats = sub.add_parser("summarize", help="analyze an exported /api/runs JSON")
    stats.add_argument("snapshot", type=Path)
    stats.add_argument("--csv", action="store_true")
    args = parser.parse_args()
    if args.command == "tasks":
        for key, spec in TASKS.items():
            print(f"{key}\t{spec['instruction']}")
    elif args.command == "command":
        print(shlex.join(["./run", *run_args(args.task, args.model, args.server_url)]))
    else:
        result = analyze(json.loads(args.snapshot.read_text()))
        if args.csv:
            import sys

            fields = [
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
            writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(result["cells"])
        else:
            print(json.dumps(result, indent=2))
