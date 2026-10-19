"""Synthetic one-hour publication counts and local median microbenchmark.

Run from the repository root with the development environment:
python -m tools.benchmark_measurement_publication
No Modbus, HTTP, Recorder database or running HA instance is contacted.
"""

import json
import time
import tracemalloc

from custom_components.fronius_pv_manager.measurement import (
    EfficiencyWindow,
    normalize_measurement,
)


def transitions(values):
    return sum(i == 0 or value != values[i - 1] for i, value in enumerate(values))


def simulate():
    power = [1000 + (index % 5 - 2) * 0.01 for index in range(720)]
    normalized = [normalize_measurement(value, "W") for value in power]
    efficiency = [96 + (index % 5 - 2) * 0.01 for index in range(720)]
    window = EfficiencyWindow()
    published = []
    for index, value in enumerate(efficiency):
        window.observe(index * 5, value, "inverter")
        if index % 6 == 0:
            published.append(window.publish())
    return {
        "scenario": "3600s, FAST=5s, SLOW=30s, deterministic +/-0.02 jitter",
        "power_before": {
            "ha_writes": 720,
            "state_changed": transitions(power),
            "unchanged_reports": 720 - transitions(power),
        },
        "power_after": {
            "ha_writes": 720,
            "state_changed": transitions(normalized),
            "unchanged_reports": 720 - transitions(normalized),
        },
        "efficiency_before": {
            "ha_writes": 720,
            "state_changed": transitions(efficiency),
        },
        "efficiency_after": {
            "slow_opportunities": len(published),
            "ha_writes": transitions(published),
            "state_changed": transitions(published),
        },
        "expected_recorder_rows": {
            "before_power_plus_efficiency": transitions(power)
            + transitions(efficiency),
            "after_power_plus_efficiency": transitions(normalized)
            + transitions(published),
        },
        "payload_requests_per_hour": {"before": 4320, "after": 4320},
    }


def benchmark():
    rounds = 100
    started = time.perf_counter()
    for _ in range(rounds):
        windows = [EfficiencyWindow() for _ in range(3)]
        for index in range(720):
            for window in windows:
                window.observe(index * 5, 96 + index % 3, "inverter")
                if index % 6 == 0:
                    window.publish()
    elapsed = time.perf_counter() - started
    tracemalloc.start()
    windows = [EfficiencyWindow() for _ in range(3)]
    for index in range(720):
        for window in windows:
            window.observe(index * 5, 96 + index % 3, "inverter")
            if index % 6 == 0:
                window.publish()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "kind": "local synthetic median-only microbenchmark, not live HA savings",
        "rounds": rounds,
        "seconds_per_simulated_hour": elapsed / rounds,
        "retained_bytes_three_windows": current,
        "peak_bytes_three_windows": peak,
        "samples_per_window": [len(w.samples) for w in windows],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {"simulation": simulate(), "median_microbenchmark": benchmark()}, indent=2
        )
    )
