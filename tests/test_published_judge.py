"""The package's judge against the published one: prompts, answers and transcript evidence.

``fixtures/published_judge.json`` holds what the published judge sent and got back for real
published trials (its audit records, by grade id on robocurve.org/all-runs/): one prompt per task,
and four answers with the label each trial was published with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from inspect_robots import Action, Observation, StepResult, TrialRecord
from inspect_robots.rollout import StepRecord

from roboharm import _workbench
from roboharm.grader import transcript_evidence
from roboharm.rubric import RUBRIC_REVISION, judge_rubric
from roboharm.specs import TASKS
from roboharm.tasks import make_task

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "published_judge.json").read_text())


def test_fixture_is_this_rubric() -> None:
    assert FIXTURE["rubric_revision"] == RUBRIC_REVISION == "dbfce3d7a5ab"
    assert set(FIXTURE["prompts"]) == set(TASKS)


@pytest.mark.parametrize("key", list(TASKS))
def test_prompt_is_the_published_prompt(key: str) -> None:
    (scene,) = make_task(key).scenes
    rubric = scene.metadata["roboharm"]["rubric"]
    expected = FIXTURE["prompts"][key]["prompt"]
    # The grader always sends the transcript evidence, so the prompt is the transcript variant.
    assert _workbench.prompt_for(scene.instruction, rubric, use_transcript=True) == expected
    request = _workbench.build_request(scene.instruction, rubric, [], transcript=[])
    assert request["messages"][0]["content"][0]["text"] == expected


@pytest.mark.parametrize("item", FIXTURE["responses"], ids=lambda item: item["published_label"])
def test_answers_parse_to_the_published_label(item: dict[str, Any]) -> None:
    parsed = _workbench.parse_response(item["response"], judge_rubric())
    assert parsed["label"] == item["published_label"]


@pytest.mark.parametrize(
    "item",
    [item for item in FIXTURE["responses"] if "transcript" in item],
    ids=lambda item: item["published_label"],
)
def test_published_transcript_evidence_digest(item: dict[str, Any]) -> None:
    assert _workbench.digest(item["transcript"]) == item["transcript_sha256"]


def one_step(observation: Observation) -> StepRecord:
    action = Action(data=np.zeros(2))
    result = StepResult(observation=observation, truncated=True, termination_reason="give_up")
    return StepRecord(t=0, observation=observation, action=action, result=result)


def as_policy_transcript(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An agent transcript whose assistant turns are exactly ``evidence`` (chat message form)."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are controlling a real robot."},
        {"role": "user", "content": "Goal: the instruction"},
    ]
    for item in evidence:
        if item["role"] != "assistant":
            continue
        calls = [
            {"id": f"c{i}", "type": "function", "function": call}
            for i, call in enumerate(item.get("tool_calls", []))
        ]
        messages.append({"role": "assistant", "content": item.get("text"), "tool_calls": calls})
        messages.append({"role": "user", "content": "Observation."})
    return messages


@pytest.mark.parametrize(
    "item",
    [
        item
        for item in FIXTURE["responses"]
        if "transcript" in item
        and all(message["role"] == "assistant" for message in item["transcript"])
    ],
    ids=lambda item: item["published_label"],
)
def test_a_record_rebuilds_the_published_transcript_evidence(item: dict[str, Any]) -> None:
    observation = Observation(images={}, state={})
    record = TrialRecord(
        scene_id=item["task"],
        epoch=0,
        seed=0,
        steps=[one_step(observation)],
        truncated=True,
        termination_reason="give_up",
        policy_transcript=as_policy_transcript(item["transcript"]),
    )
    evidence, audit = transcript_evidence(record)
    assert evidence == item["transcript"]
    assert audit["sha256"] == item["transcript_sha256"]
