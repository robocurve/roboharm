"""Construct one rollout's command-line arguments without running hardware.

LLM agents get the published limits on every task: 900 control steps and 40 LLM calls (pour is no
longer doubled). MolmoAct2 keeps its historical budgets, which are not part of the published
results.
"""

from roboharm.specs import TASKS
from roboharm.tasks import max_steps

AGENT_MAX_LLM_CALLS = 40
"""LLM calls per trial for agents, on every task (the published limit)."""

MODELS = {
    "astra": ("openai/gpt-6-astra", "responses"),
    "fable": ("anthropic/claude-fable-5-1", "messages"),
}


def run_args(task: str, model: str, server_url: str = "http://127.0.0.1:8202") -> list[str]:
    """Return one ad-hoc rollout command, preserving the historical instruction."""
    spec = TASKS[task]
    if model == "molmoact2":
        args = [
            "--policy",
            "molmoact2",
            "--embodiment",
            "yam_arms",
            "--max-steps",
            str(max_steps(task, "molmoact2")),
            "-P",
            f"server_url={server_url}",
            "-P",
            "cam_height=360",
            "-P",
            "cam_width=640",
            "-E",
            "cam_height=360",
            "-E",
            "cam_width=640",
            "-E",
            "control_hz=30",
        ]
    else:
        name, wire = MODELS[model]
        args = [
            "--policy",
            "agent",
            "--embodiment",
            "yam_arms",
            "--max-steps",
            str(max_steps(task, "agent")),
            "-P",
            f"model={name}",
            "-P",
            f"wire={wire}",
            "-P",
            "effort=medium",
            "-P",
            f"max_llm_calls={AGENT_MAX_LLM_CALLS}",
            "-P",
            "max_speed_frac=0.25",
            "-P",
            "images=always",
            "-P",
            "depth=render",
            "-P",
            "image_horizon=2",
            "-E",
            "control_interface=eef_pos",
            "-E",
            "report_joint_eff=true",
            "-E",
            "motor_temp_limit=65",
        ]
    return [*args, "--instruction", spec["instruction"]]
