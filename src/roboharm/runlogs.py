"""Shared selection of published-rubric samples and their recorded trials."""

from collections.abc import Iterable, Iterator
from itertools import zip_longest
from typing import Any

from roboharm.rubric import LABELS, RUBRIC_REVISION
from roboharm.specs import TASKS


def published_samples(
    logs: Iterable[tuple[str, dict[str, Any]]], skipped: dict[str, int]
) -> Iterator[tuple[str, dict[str, Any], str, dict[str, Any]]]:
    """Yield source, run spec, task key, and sample. Count skipped logs and samples."""
    instructions = {s["instruction"]: key for key, s in TASKS.items()}
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
        for sample in document.get("samples") or []:
            task = instructions.get(sample.get("instruction"))
            if task is None:
                skipped["other_instruction"] += 1
                continue
            scene = sample.get("scene_metadata") or {}
            if (scene.get("roboharm") or {}).get("rubric", {}).get("revision") != RUBRIC_REVISION:
                skipped["other_rubric"] += 1
                continue
            yield name, spec, task, sample


def recorded_trials(sample: dict[str, Any]) -> Iterator[tuple[str | None, bool]]:
    """Yield each recorded trial's rubric label and whether its epoch is an error row."""
    for epoch, judgement in zip_longest(
        sample.get("epochs") or [], sample.get("operator_judgements") or []
    ):
        yield judgement if judgement in LABELS else None, epoch == {}
