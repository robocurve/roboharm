"""Sampling uncertainty for rates calculated from independent trials."""

from math import sqrt
from statistics import NormalDist


def wilson_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float] | None:
    """Return the two-sided Wilson score interval for a binomial proportion.

    ``None`` represents a rate with no denominator, matching the null rates emitted by
    :mod:`roboharm.labels`. Counts describe independent Bernoulli trials: ``successes`` must be
    between zero and ``trials``. ``confidence`` is a fraction strictly between zero and one.
    """
    if isinstance(successes, bool) or not isinstance(successes, int):
        raise TypeError("successes must be an integer")
    if isinstance(trials, bool) or not isinstance(trials, int):
        raise TypeError("trials must be an integer")
    if trials < 0:
        raise ValueError("trials must be non-negative")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and trials")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    if trials == 0:
        return None

    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    estimate = successes / trials
    z_squared = z * z
    denominator = 1 + z_squared / trials
    center = (estimate + z_squared / (2 * trials)) / denominator
    margin = (
        z
        * sqrt(estimate * (1 - estimate) / trials + z_squared / (4 * trials * trials))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def confidence_intervals(rates: dict[str, tuple[int, int]], confidence: float) -> dict[str, object]:
    """Describe Wilson intervals for named ``(successes, trials)`` rate counts."""
    return {
        "method": "wilson",
        "confidence": confidence,
        "rates": {
            name: (
                None
                if (bounds := wilson_interval(successes, trials, confidence)) is None
                else {"low": bounds[0], "high": bounds[1]}
            )
            for name, (successes, trials) in rates.items()
        },
    }
