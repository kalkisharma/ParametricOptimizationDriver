# =============================================================================
# tests/test_v1_1_7.py
# Parametric Optimization Driver
# Version: v1.1.7
# Role: QA Engineer
# Last modified: 2026-05-07
# Description: Regression tests for v1.1.7 features:
#              GET /system_info, POST /uncertainty_map (Issues 4 and 8),
#              scatter_color_data in pipeline result (Issue 7),
#              _bounds stored in job store (Issue 8),
#              parallel GP fitting via n_jobs (Issue 4),
#              sobol_chart_json parallel path (Issue 4).
# =============================================================================

"""Regression tests for v1.1.7 features."""

import json
import uuid

import numpy as np
import pytest

from optimization import run_refinement, run_optimization
from sensitivity import sobol_chart_json
from surrogate import SurrogateModel
from tests.conftest import make_dataset


INPUT_COLS  = ["speed", "pitch"]
OUTPUT_COLS = ["thrust", "power", "Cm"]
BOUNDS      = [(20.0, 100.0), (-10.0, 10.0)]

BASE_CONFIG = {
    "input_cols":  INPUT_COLS,
    "output_cols": OUTPUT_COLS,
    "bounds":      {"speed": {"min": 20, "max": 100}, "pitch": {"min": -10, "max": 10}},
    "n_suggestions": 3,
    "gp_settings":  {"kernel": "matern52", "n_restarts": 1, "length_scale_type": "anisotropic"},
    "dup_threshold": 0.01,
}

OPT_CONFIG = {
    **BASE_CONFIG,
    "objective_spec": {"type": "single", "column": "speed", "direction": "maximize"},
    "constraints": [{
        "col": "power", "type": "leq", "limit_type": "constant", "limit_value": 8000.0,
        "target": 0, "tolerance": 0.001, "table_condition_cols": [], "table_limit_col": "",
    }],
    "input_constraints": [],
    "convergence_threshold": 0.001,
}

def _noop_emit(*args, **kwargs): pass


def _fitted_surrogate(n=20):
    df = make_dataset(n=n, seed=42)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values
    m = SurrogateModel(kernel="matern52", n_restarts=1, anisotropic=True)
    m.fit(X, Y, INPUT_COLS, OUTPUT_COLS)
    return m


def _inject_job(surrogate, bounds):
    """Inject a completed job into the app _jobs store; return job_id."""
    from app import _jobs
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "result": {"mode": "refinement"},
        "_surrogate": surrogate,
        "_bounds": bounds,
        "queue": None,
        "error": None,
    }
    return job_id


def _remove_job(job_id):
    from app import _jobs
    _jobs.pop(job_id, None)


# ─── GET /system_info ────────────────────────────────────────────────────────

def test_system_info_returns_cpu_count(client):
    res = client.get("/system_info")
    assert res.status_code == 200
    data = res.get_json()
    assert "cpu_count" in data
    assert isinstance(data["cpu_count"], int)
    assert data["cpu_count"] >= 1


# ─── POST /uncertainty_map — validation ──────────────────────────────────────

def test_uncertainty_map_missing_params(client):
    res = client.post("/uncertainty_map",
                      data=json.dumps({"job_id": "x"}),
                      content_type="application/json")
    assert res.status_code == 400

def test_uncertainty_map_unknown_job(client):
    res = client.post("/uncertainty_map",
                      data=json.dumps({"job_id": "no-such-job",
                                       "x_axis": "speed", "y_axis": "pitch"}),
                      content_type="application/json")
    assert res.status_code == 404

def test_uncertainty_map_no_surrogate(client):
    from app import _jobs
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"result": {"mode": "refinement"}, "_surrogate": None,
                     "_bounds": BOUNDS, "queue": None, "error": None}
    try:
        res = client.post("/uncertainty_map",
                          data=json.dumps({"job_id": job_id,
                                           "x_axis": "speed", "y_axis": "pitch"}),
                          content_type="application/json")
        assert res.status_code == 404
    finally:
        _remove_job(job_id)

def test_uncertainty_map_no_bounds(client):
    job_id = _inject_job(_fitted_surrogate(), None)
    try:
        res = client.post("/uncertainty_map",
                          data=json.dumps({"job_id": job_id,
                                           "x_axis": "speed", "y_axis": "pitch"}),
                          content_type="application/json")
        assert res.status_code == 404
    finally:
        _remove_job(job_id)

def test_uncertainty_map_invalid_axis(client):
    job_id = _inject_job(_fitted_surrogate(), BOUNDS)
    try:
        res = client.post("/uncertainty_map",
                          data=json.dumps({"job_id": job_id,
                                           "x_axis": "speed", "y_axis": "not_a_col"}),
                          content_type="application/json")
        assert res.status_code == 400
    finally:
        _remove_job(job_id)


# ─── POST /uncertainty_map — happy path ──────────────────────────────────────

def test_uncertainty_map_returns_chart(client):
    job_id = _inject_job(_fitted_surrogate(), BOUNDS)
    try:
        res = client.post("/uncertainty_map",
                          data=json.dumps({"job_id": job_id,
                                           "x_axis": "speed", "y_axis": "pitch"}),
                          content_type="application/json")
        assert res.status_code == 200
        data = res.get_json()
        assert "chart" in data
        assert "data" in data["chart"]
        assert "layout" in data["chart"]
        assert len(data["chart"]["data"]) > 0
    finally:
        _remove_job(job_id)

def test_uncertainty_map_axis_swap_changes_chart(client):
    """Swapping x and y axes produces a different heatmap (z matrix is transposed)."""
    surrogate = _fitted_surrogate()

    def _get_z(x_axis, y_axis):
        job_id = _inject_job(surrogate, BOUNDS)
        try:
            res = client.post("/uncertainty_map",
                              data=json.dumps({"job_id": job_id,
                                               "x_axis": x_axis, "y_axis": y_axis}),
                              content_type="application/json")
            return res.get_json()["chart"]["data"][0]["z"]
        finally:
            _remove_job(job_id)

    z_sp = _get_z("speed", "pitch")
    z_ps = _get_z("pitch", "speed")
    assert z_sp != z_ps


# ─── _bounds stored in job result ────────────────────────────────────────────

def test_refinement_bounds_in_raw_result():
    """_bounds must be present in the result dict run_refinement returns
    so that _run_pipeline in app.py can pop and cache it."""
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    assert "_bounds" in result
    assert len(result["_bounds"]) == len(INPUT_COLS)
    for lo, hi in result["_bounds"]:
        assert lo < hi

def test_optimization_bounds_in_raw_result():
    df = make_dataset(n=20, seed=42)
    result = run_optimization(df, {**OPT_CONFIG}, _noop_emit)
    assert "_bounds" in result
    assert len(result["_bounds"]) == len(INPUT_COLS)


# ─── scatter_color_data in pipeline result ───────────────────────────────────

def test_refinement_scatter_color_data_present():
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    assert "scatter_color_data" in result
    color_data = result["scatter_color_data"]
    for col in OUTPUT_COLS:
        assert col in color_data, f"Missing key: {col}"
        assert isinstance(color_data[col], list)
        assert len(color_data[col]) > 0

def test_optimization_scatter_color_data_present():
    df = make_dataset(n=20, seed=42)
    result = run_optimization(df, {**OPT_CONFIG}, _noop_emit)
    assert "scatter_color_data" in result
    for col in OUTPUT_COLS:
        assert col in result["scatter_color_data"]

def test_scatter_color_data_values_match_training_outputs():
    """Color values must equal the training output column values (no rows dropped here)."""
    df = make_dataset(n=20, seed=42)  # no NaN, no mask → df_clean == df
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    color_data = result["scatter_color_data"]
    np.testing.assert_allclose(
        color_data["thrust"], df["thrust"].values, rtol=1e-9,
        err_msg="scatter_color_data['thrust'] should equal training data values",
    )

def test_scatter_color_data_length_equals_clean_rows():
    """Length of color arrays equals the number of rows that survived cleaning."""
    df = make_dataset(n=20, seed=42, add_nan=True)  # row 0 has NaN → dropped
    result = run_refinement(df, {**BASE_CONFIG}, _noop_emit)
    color_data = result["scatter_color_data"]
    expected_len = df.dropna(subset=INPUT_COLS + OUTPUT_COLS).shape[0]
    for col in OUTPUT_COLS:
        assert len(color_data[col]) == expected_len, (
            f"Expected {expected_len} color values for '{col}', got {len(color_data[col])}"
        )


# ─── Parallel GP fitting ──────────────────────────────────────────────────────

def test_parallel_fit_matches_serial():
    """n_jobs=2 must produce predictions identical to n_jobs=1.

    Uses n_restarts=0 so sklearn's GP optimizer runs from a single fixed
    starting point (no random draws), making serial and parallel results
    bitwise identical regardless of thread scheduling.
    """
    df = make_dataset(n=20, seed=42)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values
    X_test = df[INPUT_COLS].values[:5]

    m1 = SurrogateModel(kernel="matern52", n_restarts=0, anisotropic=True)
    m1.fit(X, Y, INPUT_COLS, OUTPUT_COLS, n_jobs=1)

    m2 = SurrogateModel(kernel="matern52", n_restarts=0, anisotropic=True)
    m2.fit(X, Y, INPUT_COLS, OUTPUT_COLS, n_jobs=2)

    np.testing.assert_allclose(
        m1.predict(X_test), m2.predict(X_test), rtol=1e-5,
        err_msg="Parallel fit predictions differ from serial fit",
    )

def test_parallel_fit_diagnostics_match_serial():
    """LOO diagnostics from n_jobs=2 must equal n_jobs=1."""
    df = make_dataset(n=20, seed=42)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values

    m1 = SurrogateModel(kernel="matern52", n_restarts=0, anisotropic=True)
    m1.fit(X, Y, INPUT_COLS, OUTPUT_COLS, n_jobs=1)

    m2 = SurrogateModel(kernel="matern52", n_restarts=0, anisotropic=True)
    m2.fit(X, Y, INPUT_COLS, OUTPUT_COLS, n_jobs=2)

    d1, d2 = m1.loo_diagnostics(), m2.loo_diagnostics()
    for col in OUTPUT_COLS:
        assert d1[col]["kernel"] == d2[col]["kernel"], f"Kernel mismatch for {col}"
        assert abs(d1[col]["r2"] - d2[col]["r2"]) < 1e-6, (
            f"R² mismatch for {col}: serial={d1[col]['r2']} parallel={d2[col]['r2']}"
        )

def test_parallel_fit_single_output_is_safe():
    """n_jobs > 1 with one output column must not error."""
    df = make_dataset(n=15, seed=7)
    X = df[INPUT_COLS].values
    Y = df[["thrust"]].values
    m = SurrogateModel(kernel="matern52", n_restarts=1)
    m.fit(X, Y, INPUT_COLS, ["thrust"], n_jobs=4)
    assert m._fitted
    assert m.predict(X).shape == (len(X), 1)

def test_refinement_parallel_config_runs():
    """parallel=True and max_workers=2 in config must complete without error."""
    df = make_dataset(n=20, seed=42)
    result = run_refinement(df, {**BASE_CONFIG, "parallel": True, "max_workers": 2},
                            _noop_emit)
    assert len(result["suggestions"]) == BASE_CONFIG["n_suggestions"]

def test_refinement_parallel_single_output_noop():
    """parallel=True with one output (n_jobs clamped to 1) must still complete."""
    df = make_dataset(n=20, seed=42)
    cfg = {**BASE_CONFIG, "output_cols": ["thrust"],
           "parallel": True, "max_workers": 4}
    result = run_refinement(df, cfg, _noop_emit)
    assert len(result["suggestions"]) == BASE_CONFIG["n_suggestions"]


# ─── Sobol parallel ───────────────────────────────────────────────────────────

def test_sobol_chart_json_parallel_structure():
    """sobol_chart_json with n_jobs=2 returns the same structure as n_jobs=1."""
    surrogate = _fitted_surrogate()
    chart1 = sobol_chart_json(surrogate, BOUNDS, n_samples=128, n_jobs=1)
    chart2 = sobol_chart_json(surrogate, BOUNDS, n_samples=128, n_jobs=2)
    assert set(chart1.keys()) == set(chart2.keys())
    assert len(chart1["data"]) == len(chart2["data"]) == len(OUTPUT_COLS)

def test_sobol_chart_json_parallel_values_match_serial():
    """Parallel Sobol S1 values must equal serial (same fixed seed per output)."""
    surrogate = _fitted_surrogate()
    chart1 = sobol_chart_json(surrogate, BOUNDS, n_samples=256, n_jobs=1)
    chart2 = sobol_chart_json(surrogate, BOUNDS, n_samples=256, n_jobs=2)
    for t1, t2 in zip(chart1["data"], chart2["data"]):
        assert t1["name"] == t2["name"]
        np.testing.assert_allclose(
            t1["y"], t2["y"], rtol=1e-9,
            err_msg=f"Sobol S1 values differ for output '{t1['name']}'",
        )
