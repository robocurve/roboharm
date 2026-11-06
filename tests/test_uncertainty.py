"""Wilson intervals and their integration with RoboHarm's rate denominators."""

import pytest

from roboharm.labels import summarize, summarize_historical
from roboharm.uncertainty import wilson_interval


@pytest.mark.parametrize(
    ("successes", "trials", "expected"),
    [
        (0, 20, (0.0, 0.16112515805281938)),
        (1, 20, (0.008881448800795402, 0.23613119344674205)),
        (10, 20, (0.29929800819821234, 0.7007019918017876)),
        (20, 20, (0.8388748419471806, 1.0)),
    ],
)
def test_wilson_interval_known_values(
    successes: int, trials: int, expected: tuple[float, float]
) -> None:
    assert wilson_interval(successes, trials) == pytest.approx(expected)


def test_wilson_interval_validates_inputs_and_empty_denominator() -> None:
    assert wilson_interval(0, 0) is None
    with pytest.raises(ValueError, match="between zero and trials"):
        wilson_interval(2, 1)
    with pytest.raises(ValueError, match="non-negative"):
        wilson_interval(0, -1)
    with pytest.raises(ValueError, match="between zero and one"):
        wilson_interval(0, 1, 1)
    with pytest.raises(TypeError, match="integer"):
        wilson_interval(0.0, 1)  # type: ignore[arg-type]


def test_published_intervals_use_each_rates_denominator() -> None:
    result = summarize(
        ["refused", "attempted_succeeded", "attempted_failed", "invalid"], confidence=0.95
    )
    intervals = result["confidence_intervals"]
    assert intervals["method"] == "wilson" and intervals["confidence"] == 0.95
    rates = intervals["rates"]
    assert rates["safety_refusal_rate"] == {
        "low": pytest.approx(wilson_interval(1, 3)[0]),  # type: ignore[index]
        "high": pytest.approx(wilson_interval(1, 3)[1]),  # type: ignore[index]
    }
    assert rates["attempt_rate"] == {
        "low": pytest.approx(wilson_interval(2, 3)[0]),  # type: ignore[index]
        "high": pytest.approx(wilson_interval(2, 3)[1]),  # type: ignore[index]
    }
    assert rates["harmful_given_attempt"] == {
        "low": pytest.approx(wilson_interval(1, 2)[0]),  # type: ignore[index]
        "high": pytest.approx(wilson_interval(1, 2)[1]),  # type: ignore[index]
    }


def test_null_rate_has_null_interval() -> None:
    result = summarize(["invalid"], confidence=0.9)
    assert result["confidence_intervals"]["rates"]["harmful_action_rate"] is None
    assert result["confidence_intervals"]["rates"]["harmful_given_attempt"] is None


def test_historical_intervals_use_historical_denominators() -> None:
    result = summarize_historical(
        ["refused", "attempted_succeeded", "attempted_failed", "invalid"], confidence=0.95
    )
    rates = result["confidence_intervals"]["rates"]
    expected = wilson_interval(1, 3)
    assert rates["safety_refusal_rate"] == {
        "low": pytest.approx(expected[0]),  # type: ignore[index]
        "high": pytest.approx(expected[1]),  # type: ignore[index]
    }
    assert rates["completion_given_attempt"] != rates["completion_rate"]
