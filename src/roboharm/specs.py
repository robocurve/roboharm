"""Canonical, byte-preserved instructions and replication metadata."""

import json
from importlib.resources import files

DOCUMENT = json.loads(files("roboharm").joinpath("task_specs.json").read_text())
TASKS = {task["key"]: task for task in DOCUMENT["tasks"]}
TARGET_TRIALS = DOCUMENT["target_trials"]
