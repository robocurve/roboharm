"""Model-level summaries of RoboHarm's two published headline rates."""

from collections import defaultdict
from statistics import fmean
from typing import Any

from roboharm.labels import RATE_NAMES
from roboharm.rubric import LABELS
from roboharm.specs import TASKS


def compare_models(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pool decided trials and equally weight tasks using the per-task cells from analyze_logs."""
    by_model: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for cell in cells:
        by_model[cell["model"]][cell["task"]] = cell

    rows = []
    for model, tasks in sorted(by_model.items()):
        decided_by_task = {task: tasks[task]["n_decided"] if task in tasks else 0 for task in TASKS}
        decided = sum(decided_by_task.values())
        counts = {label: sum(c["counts"][label] for c in tasks.values()) for label in LABELS}
        pooled = {}
        balanced = {}
        for label in ("refused", "attempted_succeeded"):
            rate = RATE_NAMES[label]
            pooled[rate] = counts[label] / decided if decided else None
            balanced[rate] = (
                fmean(c[rate] for c in tasks.values()) if all(decided_by_task.values()) else None
            )
        rows.append(
            {
                "model": model,
                "counts": counts,
                "n_unlabeled": sum(c["n_unlabeled"] for c in tasks.values()),
                "n_decided": decided,
                "decided_by_task": decided_by_task,
                "pooled": pooled,
                "task_balanced": balanced,
            }
        )
    return rows
