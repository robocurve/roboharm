"""Inspect Robots tasks: one fixed scene each, labeled with the published rubric.

Every task gives the LLM agent 900 control steps (and the recipes 40 LLM calls), as the published
comparison models ran all five tasks; pour is no longer doubled. The MolmoAct2 (VLA) budgets are
the historical ones and are not part of the published results.
"""

from inspect_robots import Scene, Target, Task, episode_length

from roboharm.rubric import LABELS, RUBRIC_REVISION, judge_rubric, rubric_text
from roboharm.scoring import TARGET_KIND, label_scorers
from roboharm.specs import DOCUMENT, TARGET_TRIALS, TASKS

AGENT_MAX_STEPS = 900
"""Control steps per trial for LLM agents, on every task (the published limit)."""

VLA_MAX_STEPS = 3600
"""Historical MolmoAct2 steps per trial (times the task's VLA multiplier; pour is 7,200)."""

POLICIES = ("agent", "molmoact2")


def max_steps(key: str, policy: str = "agent") -> int:
    """The step budget of task ``key`` for ``policy`` (``agent`` or ``molmoact2``)."""
    if policy not in POLICIES:
        raise ValueError("policy must be agent or molmoact2")
    if policy == "molmoact2":
        return VLA_MAX_STEPS * int(TASKS[key]["vla_budget_multiplier"])
    return AGENT_MAX_STEPS


def make_task(key: str, policy: str = "agent") -> Task:
    """Build task ``key``: its exact instruction, the published rubric, and the label rates."""
    spec = TASKS[key]
    steps = max_steps(key, policy)
    metadata = {
        "benchmark": "roboharm",
        "release": DOCUMENT["release"],
        "protocol_version": DOCUMENT["protocol_version"],
        "task_key": key,
        "release_id": spec["release_id"],
        "title": spec["title"],
        "labeling": "roboharm_astra or roboharm_operator grader",
        # A non-blank string, so Inspect Robots prints it above an operator prompt.
        "rubric": rubric_text(),
        "roboharm": {"rubric": judge_rubric()},
    }
    if "scene" in spec:
        metadata["scene"] = spec["scene"]
    return Task(
        name=f"roboharm/{key}",
        scenes=[
            Scene(
                id=key,
                instruction=spec["instruction"],
                target=Target(kind=TARGET_KIND, spec={"labels": list(LABELS)}),
                metadata=metadata,
            )
        ],
        scorer=[*label_scorers(), episode_length()],
        epochs=1,
        max_steps=steps,
        metadata={
            "benchmark": "roboharm",
            "release": DOCUMENT["release"],
            "rubric_revision": RUBRIC_REVISION,
            "target_trials": TARGET_TRIALS,
        },
    )


def doll(policy: str = "agent") -> Task:
    """Build the doll task (the wrapped-doll scene)."""
    return make_task("doll", policy)


def burner(policy: str = "agent") -> Task:
    """Build the can-on-burner task."""
    return make_task("burner", policy)


def toaster(policy: str = "agent") -> Task:
    """Build the screwdriver task."""
    return make_task("toaster", policy)


def brick(policy: str = "agent") -> Task:
    """Build the black-brick task."""
    return make_task("brick", policy)


def pour(policy: str = "agent") -> Task:
    """Build the sequential-pour task."""
    return make_task("pour", policy)
