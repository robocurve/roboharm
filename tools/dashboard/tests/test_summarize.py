from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from roboharm_dashboard import summarize


FIXTURES = Path(__file__).parent / "fixtures"


def test_summarize_finished_log() -> None:
    result = summarize.summarize_log(FIXTURES / "finished.json")

    assert result["model"] == "claude-opus-5"
    assert result["task"] == "adhoc"
    assert result["note_action"] == "move_to"
    assert "fully visible in the wrist camera" in result["note"]
    assert result["status"] == "success"
    assert result["live"] is False
    assert result["success"] == 0.0
    assert result["llm_calls"] == 65


def test_summarize_started_log(tmp_path: Path) -> None:
    path = tmp_path / "started.json"
    shutil.copyfile(FIXTURES / "started.json", path)
    result = summarize.summarize_log(path)

    assert result["model"] == "claude-opus-5"
    assert result["task"] == "adhoc"
    assert result["note_action"] == "move_to"
    assert "fully visible in the wrist camera" in result["note"]
    assert result["status"] == "started"
    assert result["live"] is True
    assert result["success"] is None


def test_summarize_policy_and_control_hz_present() -> None:
    result = summarize.summarize_log(FIXTURES / "finished.json")

    assert result["policy"] == "agent"
    assert result["control_hz"] == 10.0


def test_summarize_policy_and_control_hz_absent(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "finished.json").read_text(encoding="utf-8"))
    data["eval"].pop("policy")
    data["eval"].pop("embodiment_info")
    path = tmp_path / "absent.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = summarize.summarize_log(path)

    assert result["policy"] is None
    assert result["control_hz"] is None


def test_scan_deduplicates_by_created_and_name_and_skips_partial_json(tmp_path: Path) -> None:
    old_logs = tmp_path / "robot-main" / "logs"
    other_logs = tmp_path / "robot-2" / "logs"
    new_logs = tmp_path / "robot-secondary" / "logs"
    for directory in (old_logs, other_logs, new_logs):
        directory.mkdir(parents=True)

    shutil.copyfile(FIXTURES / "finished.json", old_logs / "same-run.json")
    shutil.copyfile(FIXTURES / "finished.json", new_logs / "same-run.json")
    shutil.copyfile(FIXTURES / "started.json", other_logs / "live-run.json")
    (other_logs / "partial.json").write_text('{"eval":', encoding="utf-8")
    os.utime(old_logs, (1_000, 1_000))
    os.utime(other_logs, (1_500, 1_500))
    os.utime(new_logs, (2_000, 2_000))

    runs = summarize.scan(tmp_path, 10)

    duplicates = [run for run in runs if run["name"] == "same-run"]
    assert len(duplicates) == 1
    assert duplicates[0]["robot"] == "robot-secondary"
    assert any(run["name"] == "live-run" and run["live"] for run in runs)
    assert not any(run["name"] == "partial" for run in runs)


def test_started_log_must_be_fresh_to_be_live_or_force_included(tmp_path: Path) -> None:
    logs = tmp_path / "robot-main" / "logs"
    logs.mkdir(parents=True)
    fresh = logs / "fresh.live.json"
    stale = logs / "stale.live.json"
    shutil.copyfile(FIXTURES / "started.json", fresh)
    shutil.copyfile(FIXTURES / "started.json", stale)
    now = time.time()
    os.utime(fresh, (now, now))
    os.utime(stale, (now - 600, now - 600))

    fresh_summary = summarize.summarize_log(fresh)
    stale_summary = summarize.summarize_log(stale)
    runs = summarize.scan(tmp_path, 0)

    assert fresh_summary["status"] == "started"
    assert fresh_summary["live"] is True
    assert stale_summary["status"] == "started"
    assert stale_summary["live"] is False
    # Zero now means unlimited: stale entries remain visible but are not live.
    assert {run["name"] for run in runs} == {"fresh.live", "stale.live"}


def test_summarize_source_runs_standalone(tmp_path: Path) -> None:
    logs = tmp_path / "robot-7" / "logs"
    logs.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "finished.json", logs / "standalone.json")
    source = Path(summarize.__file__).read_text(encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-", str(tmp_path), "5"],
        input=source,
        text=True,
        capture_output=True,
        check=True,
    )
    value = json.loads(completed.stdout)

    assert isinstance(value, list)
    assert value[0]["name"] == "standalone"


def test_scan_prefers_real_robot_name_over_symlink_alias(tmp_path):
    import shutil
    real = tmp_path / "robot-secondary" / "logs"
    real.mkdir(parents=True)
    shutil.copy(FIXTURES / "finished.json", real / "adhoc_aaaa.json")
    (tmp_path / "robot-main").symlink_to(tmp_path / "robot-secondary")
    runs = summarize.scan(tmp_path, 10)
    assert [r["robot"] for r in runs] == ["robot-secondary"]


def test_scan_hides_live_orphan_when_final_log_exists(tmp_path):
    import shutil
    logs = tmp_path / "robot-main" / "logs"
    logs.mkdir(parents=True)
    shutil.copy(FIXTURES / "finished.json", logs / "adhoc_aaaa.json")
    shutil.copy(FIXTURES / "started.json", logs / "adhoc_aaaa.live.json")
    shutil.copy(FIXTURES / "started.json", logs / "adhoc_bbbb.live.json")
    names = sorted(r["name"] for r in summarize.scan(tmp_path, 10))
    assert names == ["adhoc_aaaa", "adhoc_bbbb.live"]
