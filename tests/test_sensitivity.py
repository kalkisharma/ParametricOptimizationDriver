"""Tests for sensitivity.py: Sobol S1 properties and dominant-input identification."""

import numpy as np
import pytest

from sensitivity import sobol_first_order, sobol_chart_json
from surrogate import SurrogateModel
from tests.conftest import make_dataset


INPUT_COLS  = ["speed", "pitch"]
OUTPUT_COLS = ["power"]
BOUNDS      = [(20.0, 100.0), (-10.0, 10.0)]


def _fit_power_model(n=30):
    """power = speed^3 * 0.01 — speed is the dominant input."""
    df = make_dataset(n=n, seed=42)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values
    m = SurrogateModel(kernel="matern52", n_restarts=1, anisotropic=False)
    m.fit(X, Y, INPUT_COLS, OUTPUT_COLS)
    return m


def test_sobol_returns_all_inputs():
    m = _fit_power_model()
    s1 = sobol_first_order(m, BOUNDS, "power", n_samples=256)
    assert set(s1.keys()) == set(INPUT_COLS)

def test_sobol_non_negative():
    m = _fit_power_model()
    s1 = sobol_first_order(m, BOUNDS, "power", n_samples=256)
    for col, val in s1.items():
        assert val >= 0.0, f"Negative S1 for {col}: {val}"

def test_sobol_sum_leq_one():
    m = _fit_power_model()
    s1 = sobol_first_order(m, BOUNDS, "power", n_samples=512)
    total = sum(s1.values())
    assert total <= 1.05, f"S1 sum > 1: {total}"

def test_sobol_speed_dominates_power():
    """Speed should have higher S1 than pitch for the power output (power ∝ speed³)."""
    m = _fit_power_model(n=40)
    s1 = sobol_first_order(m, BOUNDS, "power", n_samples=1024)
    assert s1["speed"] > s1["pitch"], (
        f"Expected speed to dominate power, but S1(speed)={s1['speed']:.3f} "
        f"< S1(pitch)={s1['pitch']:.3f}"
    )

def test_sobol_chart_json_structure():
    m = _fit_power_model()
    chart = sobol_chart_json(m, BOUNDS, n_samples=256)
    assert "data" in chart
    assert "layout" in chart
    assert len(chart["data"]) == len(OUTPUT_COLS)
