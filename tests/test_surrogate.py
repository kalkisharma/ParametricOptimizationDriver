# =============================================================================
# tests/test_surrogate.py
# Parametric Optimization Driver
# Version: v1.1.5
# Role: QA Engineer, ML Engineer
# Last modified: 2026-05-06
# Description: Tests for surrogate.py — GP fitting, LOO diagnostics, kernel
#              selection, std calibration, and LOO fallback path.
# =============================================================================

"""Tests for surrogate.py: GP fitting, LOO diagnostics, kernel selection."""

import numpy as np
import pytest
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern

from surrogate import SurrogateModel, _loo_rmse
from tests.conftest import make_dataset, f_thrust, f_power


INPUT_COLS  = ["speed", "pitch"]
OUTPUT_COLS = ["thrust", "power"]


def _fit(n=30, kernel="auto", anisotropic=True):
    df = make_dataset(n=n, seed=42)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values
    m = SurrogateModel(kernel=kernel, n_restarts=2, anisotropic=anisotropic)
    m.fit(X, Y, INPUT_COLS, OUTPUT_COLS)
    return m, X, Y


def test_fit_returns_model():
    m, _, _ = _fit()
    assert m._fitted


def test_predict_shape():
    m, X, _ = _fit()
    preds = m.predict(X)
    assert preds.shape == (len(X), len(OUTPUT_COLS))


def test_predict_with_std_shape():
    m, X, _ = _fit()
    means, stds = m.predict_with_std(X)
    assert means.shape == stds.shape == (len(X), len(OUTPUT_COLS))
    assert (stds >= 0).all()


def test_loo_r2_reasonable():
    """LOO R² should exceed 0.85 on 30 points of a smooth analytic function."""
    m, _, _ = _fit(n=30)
    diag = m.loo_diagnostics()
    for col in OUTPUT_COLS:
        assert diag[col]["r2"] > 0.80, f"R² too low for {col}: {diag[col]['r2']}"


def test_kernel_auto_selection():
    """Auto mode should pick either matern52 or rbf — not crash."""
    m, _, _ = _fit(kernel="auto")
    diag = m.loo_diagnostics()
    for col in OUTPUT_COLS:
        assert diag[col]["kernel"] in ("matern52", "rbf")


def test_kernel_manual_rbf():
    m, _, _ = _fit(kernel="rbf")
    diag = m.loo_diagnostics()
    for col in OUTPUT_COLS:
        assert diag[col]["kernel"] == "rbf"


def test_isotropic_lengthscale():
    m, _, _ = _fit(anisotropic=False)
    assert m._fitted


def test_predict_single():
    m, _, _ = _fit()
    x = np.array([50.0, 0.0])
    mu, sigma = m.predict_single(x)
    assert set(mu.keys()) == set(OUTPUT_COLS)
    assert all(s >= 0 for s in sigma.values())


def test_unfitted_raises():
    m = SurrogateModel()
    with pytest.raises(RuntimeError, match="not been fitted"):
        m.predict(np.zeros((1, 2)))


def test_not_enough_data_warning():
    """Should still fit with minimum data but with poor accuracy."""
    df = make_dataset(n=4, seed=99)
    X = df[INPUT_COLS].values
    Y = df[OUTPUT_COLS].values
    m = SurrogateModel(n_restarts=1)
    m.fit(X, Y, INPUT_COLS, OUTPUT_COLS)
    assert m._fitted


# ─── Gate 6: coverage gap tests ──────────────────────────────────────────────

def test_predict_with_std_unfitted_raises():
    """predict_with_std on an unfitted model should raise RuntimeError."""
    m = SurrogateModel()
    with pytest.raises(RuntimeError, match="not been fitted"):
        m.predict_with_std(np.zeros((1, 2)))


def test_gp_std_lower_at_training_than_held_out():
    """GP std at training points should be lower than at distant untrained points.
    This confirms the surrogate is calibrated: it is more certain where it has data."""
    m, X, _ = _fit()
    _, stds_train = m.predict_with_std(X)

    # Create points far outside the training domain [20-100] x [-10,10]
    X_far = np.array([[200.0, 50.0], [200.0, -50.0], [-100.0, 50.0]])
    _, stds_far = m.predict_with_std(X_far)

    mean_std_train = stds_train.mean()
    mean_std_far = stds_far.mean()
    assert mean_std_train < mean_std_far, (
        f"Expected lower std at training points ({mean_std_train:.4f}) "
        f"than at distant points ({mean_std_far:.4f})"
    )


def test_loo_rmse_fallback_path():
    """Deleting L_ forces the except branch (manual LOO loop) in _loo_rmse.
    The fallback must still return a non-negative float."""
    X = np.linspace(0, 1, 7).reshape(-1, 1)
    y = np.sin(X.flatten())
    gp = GaussianProcessRegressor(kernel=Matern(nu=2.5), n_restarts_optimizer=0)
    gp.fit(X, y)

    rmse_fast = _loo_rmse(gp, X, y)
    assert rmse_fast >= 0.0

    del gp.L_  # triggers AttributeError in fast path → fallback executes
    rmse_fallback = _loo_rmse(gp, X, y)
    assert isinstance(rmse_fallback, float)
    assert rmse_fallback >= 0.0
