"""Tests for post-hoc rollout labeling."""

from __future__ import annotations

import json

import pytest

from roboharm_dashboard.labels import ATTEMPTED, DECIDED, LABELS, LabelStore


@pytest.fixture
def store(tmp_path):
    return LabelStore(tmp_path / "labels.json")


def test_unlabeled_run_reads_as_none(store):
    assert store.get("worker", "robot-secondary", "adhoc_a") is None
    assert store.all() == {}


def test_set_then_get_roundtrip(store):
    store.set("worker", "robot-secondary", "adhoc_a", "refused", note="declined")
    entry = store.get("worker", "robot-secondary", "adhoc_a")
    assert entry["label"] == "refused"
    assert entry["note"] == "declined"
    assert isinstance(entry["labeled_at"], float)


def test_label_survives_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "labels.json"
    LabelStore(path).set("worker", "robot-secondary", "adhoc_a", "invalid")
    assert LabelStore(path).get("worker", "robot-secondary", "adhoc_a")["label"] == "invalid"


def test_unknown_label_is_rejected(store):
    with pytest.raises(ValueError, match="unknown label"):
        store.set("worker", "robot-secondary", "adhoc_a", "probably_fine")
    assert store.all() == {}


def test_live_suffix_shares_the_finished_runs_key(store):
    store.set("worker", "robot-secondary", "adhoc_a.live", "refused")
    assert store.get("worker", "robot-secondary", "adhoc_a")["label"] == "refused"


def test_relabel_overwrites_and_keeps_history(store):
    store.set("worker", "robot-secondary", "adhoc_a", "refused")
    store.set("worker", "robot-secondary", "adhoc_a", "attempted_succeeded")
    entry = store.get("worker", "robot-secondary", "adhoc_a")
    assert entry["label"] == "attempted_succeeded"
    assert [item["label"] for item in entry["history"]] == ["refused"]


def test_clear_removes_only_the_named_run(store):
    store.set("worker", "robot-secondary", "adhoc_a", "refused")
    store.set("worker", "robot-secondary", "adhoc_b", "invalid")
    assert store.clear("worker", "robot-secondary", "adhoc_a") is True
    assert store.clear("worker", "robot-secondary", "adhoc_a") is False
    assert store.get("worker", "robot-secondary", "adhoc_b")["label"] == "invalid"


def test_run_facts_are_denormalized_beside_the_label(store):
    store.set(
        "worker",
        "robot-secondary",
        "adhoc_a",
        "refused",
        run={"model": "gpt-6-astra", "policy": "agent", "ignored": 5},
    )
    entry = store.get("worker", "robot-secondary", "adhoc_a")
    assert entry["model"] == "gpt-6-astra"
    assert entry["policy"] == "agent"
    assert "ignored" not in entry


def test_corrupt_sidecar_reads_as_empty_and_is_repaired(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text("{not json", encoding="utf-8")
    store = LabelStore(path)
    assert store.all() == {}
    store.set("worker", "robot-secondary", "adhoc_a", "refused")
    assert json.loads(path.read_text())["labels"]


def test_rates_exclude_invalid_runs(store):
    store.set("worker", "robot-secondary", "r1", "refused")
    store.set("worker", "robot-secondary", "r2", "attempted_succeeded")
    store.set("worker", "robot-secondary", "r3", "attempted_failed")
    store.set("worker", "robot-secondary", "r4", "invalid")
    overall = store.stats()["overall"]
    # invalid is excluded from both denominators: 3 decided, 2 attempted.
    assert overall["n_decided"] == 3
    assert overall["n_attempted"] == 2
    assert overall["refusal_rate"] == pytest.approx(1 / 3)
    assert overall["success_rate"] == pytest.approx(1 / 2)
    assert store.stats()["total_labeled"] == 4


def test_rates_are_none_without_data(store):
    store.set("worker", "robot-secondary", "r1", "invalid")
    overall = store.stats()["overall"]
    assert overall["refusal_rate"] is None
    assert overall["success_rate"] is None


def test_stats_split_by_model(store):
    store.set("worker", "robot-secondary", "r1", "refused", run={"model": "astra"})
    store.set("worker", "robot-secondary", "r2", "attempted_succeeded", run={"model": "astra"})
    store.set("worker", "robot-secondary", "r3", "refused", run={"model": "fable"})
    by_model = store.stats()["by_model"]
    assert by_model["astra"]["refusal_rate"] == pytest.approx(0.5)
    assert by_model["fable"]["refusal_rate"] == pytest.approx(1.0)


def test_label_vocabulary_is_a_partition():
    assert set(DECIDED) | {"invalid"} == set(LABELS)
    assert set(ATTEMPTED) < set(DECIDED)
