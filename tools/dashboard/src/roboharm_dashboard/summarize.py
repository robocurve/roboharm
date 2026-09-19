"""Summarize inspect-robots EvalLogs.

This module deliberately uses only the Python standard library: collectors pipe
its source directly to ``python3 -`` on remote robot hosts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any


LOGGER = logging.getLogger(__name__)
_STARTED_RE = re.compile(br'"status"\s*:\s*"started"')
_PEEK_SIZE = 16 * 1024
_LIVE_MTIME_WINDOW_S = 120.0


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(value: Any) -> Any:
    values = _sequence(value)
    return values[0] if values else None


def _is_fresh(mtime: float) -> bool:
    return mtime >= time.time() - _LIVE_MTIME_WINDOW_S


def _latest_note(sample: dict[str, Any]) -> tuple[Any, Any]:
    transcripts = _sequence(sample.get("policy_transcripts"))
    if not transcripts:
        return None, None
    transcript = _sequence(transcripts[0])
    for message in reversed(transcript):
        message = _mapping(message)
        if message.get("role") != "assistant":
            continue
        calls = _sequence(message.get("tool_calls"))
        if not calls:
            continue
        function = _mapping(_mapping(calls[0]).get("function"))
        action = function.get("name")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        arguments = _mapping(arguments)
        return arguments.get("note"), action
    return None, None


def _summarize(data: dict[str, Any], path: Path) -> dict[str, Any]:
    eval_data = _mapping(data.get("eval"))
    if not eval_data:
        raise ValueError("not an EvalLog")

    embodiment_info = _mapping(eval_data.get("embodiment_info"))
    policy_config = _mapping(eval_data.get("policy_config"))
    samples = _sequence(data.get("samples"))
    sample = _mapping(samples[0]) if samples else {}
    stats = _mapping(data.get("stats"))
    results = _mapping(data.get("results"))
    metrics = _mapping(results.get("metrics"))
    trial_metadata = _sequence(sample.get("trial_metadata"))
    trial = _mapping(trial_metadata[0]) if trial_metadata else {}
    llm_usage = _mapping(trial.get("llm_usage"))
    note, note_action = _latest_note(sample)

    mtime = path.stat().st_mtime
    absolute_path = os.path.abspath(path)
    robot_dir = path.parent.parent
    html_relative = Path("logs") / "html" / f"{path.stem}.html"
    html = html_relative.as_posix() if (robot_dir / html_relative).is_file() else None
    status = data.get("status")

    return {
        "host": None,
        "robot": robot_dir.name,
        "name": path.stem,
        "path": absolute_path,
        "status": status,
        "live": status == "started" and _is_fresh(mtime),
        "model": policy_config.get("model"),
        "policy": eval_data.get("policy"),
        "control_hz": embodiment_info.get("control_hz"),
        "effort": policy_config.get("effort"),
        "task": eval_data.get("task"),
        "instruction": sample.get("instruction"),
        "started_at": stats.get("started_at"),
        "completed_at": stats.get("completed_at"),
        "duration_s": stats.get("duration_s"),
        "total_steps": stats.get("total_steps"),
        "max_steps": eval_data.get("max_steps"),
        "success": metrics.get("success_at_end"),
        "judgement": _first(sample.get("operator_judgements")),
        "termination": _first(sample.get("termination_reasons")),
        "error": data.get("error"),
        "note": note,
        "note_action": note_action,
        "llm_calls": llm_usage.get("llm_calls"),
        "frames_dir": stats.get("frames_dir"),
        "html": html,
        "mtime": mtime,
    }


def _load_summary(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("not an EvalLog")
    summary = _summarize(data, path)
    created = _mapping(data.get("eval")).get("created")
    return summary, str(created or "")


def summarize_log(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return the dashboard fields for one complete EvalLog JSON file."""

    return _load_summary(Path(path))[0]


def _looks_started(path: Path) -> bool:
    """Cheaply inspect an older, recently rewritten log for live status."""

    try:
        stat = path.stat()
        if not _is_fresh(stat.st_mtime):
            return False
        size = stat.st_size
        with path.open("rb") as file:
            beginning = file.read(_PEEK_SIZE)
            if size > _PEEK_SIZE:
                file.seek(max(0, size - _PEEK_SIZE))
                ending = file.read(_PEEK_SIZE)
            else:
                ending = b""
        return bool(_STARTED_RE.search(beginning) or _STARTED_RE.search(ending))
    except OSError as exc:
        LOGGER.debug("could not peek at %s: %s", path, exc)
        return False


def scan(root: str | os.PathLike[str], limit: int) -> list[dict[str, Any]]:
    """Scan ``root/robot-*/logs`` and return recent, deduplicated summaries.

    A non-positive ``limit`` scans every log. Capping the scan silently
    undercounts a robot that has more logs than the cap: each new run evicts an
    older one, so a per-model tally appears frozen while runs keep landing.
    """

    root_path = Path(root)
    limit = int(limit)
    unlimited = limit <= 0
    candidates: list[tuple[dict[str, Any], str, float]] = []
    scanned_real_robots: set[str] = set()

    for robot_dir in sorted(root_path.glob("robot-*")):
        logs_dir = robot_dir / "logs"
        if not logs_dir.is_dir():
            continue
        real_robot = os.path.realpath(robot_dir)
        if real_robot in scanned_real_robots:
            LOGGER.debug("skipping duplicate robot path %s", robot_dir)
            continue
        if robot_dir.is_symlink() and Path(real_robot).parent == root_path.resolve():
            # Alias of a sibling robot directory; the real name wins.
            LOGGER.debug("skipping symlink alias %s -> %s", robot_dir, real_robot)
            continue
        scanned_real_robots.add(real_robot)
        try:
            logs_mtime = logs_dir.stat().st_mtime
            discovered = list(logs_dir.glob("*.json"))
            # A crashed run leaves `<name>.live.json` behind; hide it once the
            # final `<name>.json` exists (LiveLogSink normally removes it).
            names = {path.name for path in discovered}
            discovered = [
                path
                for path in discovered
                if not (path.name.endswith(".live.json") and path.name[: -len(".live.json")] + ".json" in names)
            ]
        except OSError as exc:
            LOGGER.debug("could not list %s: %s", logs_dir, exc)
            continue
        path_mtimes: list[tuple[float, Path]] = []
        for path in discovered:
            try:
                path_mtimes.append((path.stat().st_mtime, path))
            except OSError as exc:
                LOGGER.debug("could not stat %s: %s", path, exc)
        paths = [path for _mtime, path in sorted(path_mtimes, reverse=True)]

        if unlimited:
            selected = list(paths)
        else:
            selected = list(paths[:limit])
            for path in paths[limit:]:
                if _looks_started(path):
                    selected.append(path)

        for path in selected:
            try:
                summary, created = _load_summary(path)
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
                LOGGER.debug("skipping unreadable or non-EvalLog %s: %s", path, exc)
                continue
            candidates.append((summary, created, logs_mtime))

    deduplicated: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
    for summary, created, logs_mtime in candidates:
        run_id = (created, str(summary["name"]))
        previous = deduplicated.get(run_id)
        if previous is None or logs_mtime > previous[1]:
            deduplicated[run_id] = (summary, logs_mtime)

    return sorted(
        (item[0] for item in deduplicated.values()),
        key=lambda summary: float(summary.get("mtime") or 0.0),
        reverse=True,
    )


def _main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: summarize.py ROOT LIMIT")
    print(json.dumps(scan(sys.argv[1], int(sys.argv[2]))))


if __name__ == "__main__":
    _main()
