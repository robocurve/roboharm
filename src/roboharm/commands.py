"""Construct the recovered experiment arguments without running hardware."""

from roboharm.specs import TASKS

MODELS = {
    "astra": ("openai/gpt-6-astra", "responses"),
    "fable": ("anthropic/claude-fable-5-1", "messages"),
}


def run_args(task: str, model: str, server_url: str = "http://127.0.0.1:8202") -> list[str]:
    """Return one ad-hoc rollout command, preserving the historical instruction."""
    spec = TASKS[task]
    factor = spec["budget_multiplier"]
    if model == "molmoact2":
        args = [
            "--policy",
            "molmoact2",
            "--embodiment",
            "yam_arms",
            "--max-steps",
            str(3600 * factor),
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
            str(900 * factor),
            "-P",
            f"model={name}",
            "-P",
            f"wire={wire}",
            "-P",
            "effort=medium",
            "-P",
            f"max_llm_calls={40 * factor}",
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
        ]
    return [*args, "--instruction", spec["instruction"]]
