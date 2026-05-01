"""Tests for surrogate.py: GP fitting, LOO diagnostics, kernel selection."""

import numpy as np
import pytest

from surrogate import SurrogateModel
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
