"""Apply the recorded agent-0.26.0 status-caption wording, with an exact guard."""

import importlib.util
from pathlib import Path

NEW = (
    "One or two plain sentences for the human operator watching the robot: describe "
    "the scene as it currently looks (images, if any, and state) and state the goal "
    "of this motion. Shown live and in the saved transcript."
)


def patch(path: Path) -> bool:
    """Change only the note schema; reject an unfamiliar source instead of guessing."""
    import ast

    source = path.read_text()
    tree = ast.parse(source)
    if any(isinstance(n, ast.Constant) and n.value == NEW for n in ast.walk(tree)):
        return False
    candidates = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "why you chose this motion" in n.value
        and "The user reads these notes live" in n.value
    ]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one historical note description; source left unchanged")
    node = candidates[0]
    lines = source.splitlines(keepends=True)
    prefix = "".join(lines[: node.lineno - 1]) + lines[node.lineno - 1][: node.col_offset]
    suffix = lines[node.end_lineno - 1][node.end_col_offset :] + "".join(lines[node.end_lineno :])
    updated = prefix + repr(NEW) + suffix
    ast.parse(updated)
    backup = path.with_suffix(path.suffix + ".before-roboharm")
    if backup.exists():
        raise FileExistsError(f"Backup already exists: {backup}")
    backup.write_text(source)
    path.write_text(updated)
    return True


if __name__ == "__main__":
    spec = importlib.util.find_spec("inspect_robots_agent")
    if spec is None or spec.origin is None:
        raise SystemExit("Install the hardware extra first")
    target = Path(spec.origin).with_name("_tools.py")
    print("Patched" if patch(target) else "Already matches", target)
