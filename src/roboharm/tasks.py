"""Inspect Robots tasks: one supervised rollout, labeled afterward."""

from inspect_robots import Scene, Task, episode_length

from roboharm.specs import TASKS


def make_task(key: str, policy: str = "agent", arm: str = "harmful") -> Task:
    """Set the historical action budget without pretending to score safety online."""
    if policy not in {"agent", "molmoact2"}:
        raise ValueError("policy must be agent or molmoact2")
    if arm not in {"harmful", "benign"}:
        raise ValueError("arm must be harmful or benign")
    spec = TASKS[key]
    instruction = spec["instruction"] if arm == "harmful" else spec["benign_control"]
    completion = spec["completion"] if arm == "harmful" else spec["benign_completion"]
    return Task(
        name=f"roboharm/{key}",
        scenes=[
            Scene(
                id=key if arm == "harmful" else f"{key}-benign",
                instruction=instruction,
                metadata={
                    "benchmark": "roboharm",
                    "protocol_version": "0.1.0",
                    "task_key": key,
                    "arm": arm,
                    "paired_instruction": (
                        spec["benign_control"] if arm == "harmful" else spec["instruction"]
                    ),
                    "labeling": "post-hoc",
                    "completion": completion,
                },
            )
        ],
        scorer=episode_length(),
        epochs=1,
        max_steps=(3600 if policy == "molmoact2" else 900) * spec["budget_multiplier"],
        metadata={"target_trials": 20, "labeling": "post-hoc", "arm": arm},
    )


def doll(policy: str = "agent", arm: str = "harmful") -> Task:
    """Build the doll task."""
    return make_task("doll", policy, arm)


def burner(policy: str = "agent", arm: str = "harmful") -> Task:
    """Build the can-on-burner task."""
    return make_task("burner", policy, arm)


def toaster(policy: str = "agent", arm: str = "harmful") -> Task:
    """Build the screwdriver task."""
    return make_task("toaster", policy, arm)


def brick(policy: str = "agent", arm: str = "harmful") -> Task:
    """Build the black-brick task."""
    return make_task("brick", policy, arm)


def pour(policy: str = "agent", arm: str = "harmful") -> Task:
    """Build the sequential-pour task."""
    return make_task("pour", policy, arm)
