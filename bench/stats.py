"""Small statistics helpers shared by the benchmark runners."""

from __future__ import annotations

import statistics


def percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarize(samples_us: list[float]) -> dict[str, float]:
    return {
        "mean_us": statistics.fmean(samples_us),
        "p50_us": percentile(samples_us, 50),
        "p95_us": percentile(samples_us, 95),
        "p99_us": percentile(samples_us, 99),
        "ops_per_s": 1e6 / statistics.fmean(samples_us),
    }
