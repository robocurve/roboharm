"""The docs show the rubric, instructions and budgets the package uses."""

import re
from pathlib import Path

import pytest

from roboharm.rubric import judge_rubric
from roboharm.specs import TASKS

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_labeling_page_quotes_the_rubric_word_for_word() -> None:
    page = (ROOT / "docs" / "labeling.md").read_text()
    guidance = re.search(r"```text\n(.*?)\n```", page, re.S)
    assert guidance is not None and guidance.group(1) == judge_rubric()["guidance"]
    for label in judge_rubric()["labels"]:
        row = f"| `{label['value']}` | {label['title']} | {label['description']} |"
        assert row in page


@pytest.mark.parametrize("key", list(TASKS))
def test_readme_lists_task(key: str) -> None:
    readme = (ROOT / "README.md").read_text()
    assert f"| [`roboharm/{key}`](docs/tasks/{key}.md) | `{TASKS[key]['instruction']}` |" in readme


@pytest.mark.parametrize("key", list(TASKS))
def test_task_page_budget_and_instruction(key: str) -> None:
    page = (ROOT / "docs" / "tasks" / f"{key}.md").read_text()
    assert f"> {TASKS[key]['instruction']}\n" in page
    assert "- Agent models: 900 steps and 40 LLM calls" in page


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_em_dashes(path: Path) -> None:
    assert "—" not in path.read_text()


def test_published_id_lists() -> None:
    graded = (ROOT / "docs/published/graded-with-this-judge.txt").read_text().split()
    other = (ROOT / "docs/published/ran-with-other-limits.txt").read_text().split()
    assert (len(graded), len(set(graded)), len(other), len(set(other))) == (220, 220, 31, 31)
    assert all(re.fullmatch(r"[0-9a-f]{32}", grade_id) for grade_id in graded + other)
