"""Tests for optimization.py: full pipeline, cold-start LHS, convergence, timing."""

import time
import numpy as np
import pandas as pd
import pytest

from optimization import run_refinement, run_optimization, _lhs_design
from tests.conftest import make_dataset, make_simple_dataset


INPUT_COLS  = ["speed", "pitch"]
OUTPUT_COLS = ["thrust", "power", "Cm"]

BASE_CONFIG = {
    "input_cols":  INPUT_COLS,
    "output_cols": OUTPUT_COLS,
    "bounds":      {"speed": {"min": 20, "max": 100}, "pitch": {"min": -10, "max": 10}},
    "n_suggestions": 3,
    "gp_settings":  {"kernel": "matern52", "n_restarts": 1, "length_scale_type": "anisotropic"},
    "dup_threshold": 0.01,
}

def _noop_emit(*args, **kwargs): pass


# ─── LHS cold start ──────────────────────────────────────────────────────────

def test_lhs_design_shape():
    bounds = [(0.0, 1.0), (0.0, 2.0), (0.0, 3.0)]
    X = _lhs_design(bounds, n=10, integer_dims=[])
    assert X.shape == (10, 3)

def test_lhs_design_within_bounds():
    bounds = [(20.0, 100.0), (-10.0, 10.0)]
    X = _lhs_design(bounds, n=15, integer_dims=[])
    assert (X[:, 0] >= 20).all() and (X[:, 0] <= 100).all()
    assert (X[:, 1] >= -10).all() and (X[:, 1] <= 10).all()

def test_lhs_integer_rounding():
    bounds = [(0.0, 5.0), (0.0, 1.0)]
    X = _lhs_design(bounds, n=8, integer_dims=[0])
    for v in X[:, 0]:
        assert abs(v - round(v)) < 1e-9


# ─── Refinement mode ─────────────────────────────────────────────────────────

def test_refinement_returns_correct_keys():
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    assert "suggestions" in result
    assert "diagnostics" in result
    assert "plots" in result

def test_refinement_suggestion_count():
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG, "n_suggestions": 4}, _noop_emit)
    assert len(result["suggestions"]) == 4

def test_refinement_suggestions_within_bounds():
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    for row in result["suggestions"]:
        assert 20 <= row["speed"] <= 100
        assert -10 <= row["pitch"] <= 10

def test_refinement_cold_start():
    df = pd.DataFrame({"speed": [], "pitch": [], "thrust": [], "power": [], "Cm": []})
    result = run_refinement(df, {**BASE_CONFIG, "n_suggestions": 5}, _noop_emit)
    assert result["mode"] == "cold_start"
    assert len(result["suggestions"]) == 5

def test_refinement_insufficient_data_raises():
    df = make_dataset(n=2, seed=0)
    with pytest.raises(ValueError, match="Not enough data"):
        run_refinement(df, BASE_CONFIG, _noop_emit)


# ─── Optimization mode ───────────────────────────────────────────────────────

OPT_CONFIG = {
    **BASE_CONFIG,
    "objective_spec": {"type": "single", "column": "speed", "direction": "maximize"},
    "constraints": [
        {"col": "power", "type": "leq", "limit_type": "constant", "limit_value": 8000.0,
         "target": 0, "tolerance": 0.001, "table_condition_cols": [], "table_limit_col": ""},
        {"col": "Cm", "type": "eq", "target": 0.0, "tolerance": 0.1,
         "limit_type": "constant", "limit_value": None,
         "table_condition_cols": [], "table_limit_col": ""},
    ],
    "input_constraints": [],
    "convergence_threshold": 0.001,
}

def test_optimization_returns_correct_keys():
    df = make_dataset(n=20, seed=42)
    result = run_optimization(df, {**OPT_CONFIG}, _noop_emit)
    assert "suggestions" in result
    assert "convergence" in result

def test_optimization_suggestion_count():
    df = make_dataset(n=20, seed=42)
    result = run_optimization(df, {**OPT_CONFIG, "n_suggestions": 3}, _noop_emit)
    assert len(result["suggestions"]) == 3

def test_optimization_has_predictions():
    df = make_dataset(n=20, seed=42)
    result = run_optimization(df, {**OPT_CONFIG, "n_suggestions": 2}, _noop_emit)
    for row in result["suggestions"]:
        assert any(k.startswith("pred_") for k in row)

def test_optimization_feasibility_mode_no_feasible():
    df = make_dataset(n=20, seed=42)
    cfg = {**OPT_CONFIG, "constraints": [{
        "col": "power", "type": "leq", "limit_type": "constant", "limit_value": -999.0,
        "target": 0, "tolerance": 0.001, "table_condition_cols": [], "table_limit_col": "",
    }]}
    result = run_optimization(df, cfg, _noop_emit)
    assert result.get("feasibility_mode") is True


# ─── Performance assertions ──────────────────────────────────────────────────

def test_performance_refinement_50_rows():
    df = make_dataset(n=50, seed=42)
    t0 = time.time()
    run_refinement(df, {**BASE_CONFIG, "n_suggestions": 5}, _noop_emit)
    elapsed = time.time() - t0
    assert elapsed < 60, f"Refinement took too long: {elapsed:.1f}s"

def test_performance_outlier_detection_100_rows():
    from preprocessing import detect_outliers
    df = make_dataset(n=100, seed=42)
    all_cols = INPUT_COLS + OUTPUT_COLS
    t0 = time.time()
    detect_outliers(df, all_cols)
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"Outlier detection too slow: {elapsed:.2f}s"
