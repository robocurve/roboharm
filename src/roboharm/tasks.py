"""Inspect Robots tasks: one supervised rollout, labeled afterward."""

from inspect_robots import Scene, Task, episode_length

from roboharm.specs import TASKS


def make_task(key: str, policy: str = "agent") -> Task:
    """Set the historical action budget without pretending to score safety online."""
    if policy not in {"agent", "molmoact2"}:
        raise ValueError("policy must be agent or molmoact2")
    spec = TASKS[key]
    return Task(
        name=f"roboharm/{key}",
        scenes=[
            Scene(
                id=key,
                instruction=spec["instruction"],
                metadata={
                    "benchmark": "roboharm",
                    "protocol_version": "0.1.0",
                    "task_key": key,
                    "labeling": "post-hoc",
                    "completion": spec["completion"],
                },
            )
        ],
        scorer=episode_length(),
        epochs=1,
        max_steps=(3600 if policy == "molmoact2" else 900) * spec["budget_multiplier"],
        metadata={"target_trials": 20, "labeling": "post-hoc"},
    )


def doll(policy: str = "agent") -> Task:
    """Build the doll task."""
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
