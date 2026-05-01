"""
Test fixtures: analytic CFD-like functions with known ground truth,
Flask test client, and reusable dataset builders.
"""

import numpy as np
import pandas as pd
import pytest

from app import app as flask_app


# ─── Analytic ground-truth functions ────────────────────────────────────────

def f_thrust(speed, pitch):
    """thrust = sin(pitch_rad) * speed^2 * 0.05"""
    return np.sin(np.deg2rad(pitch)) * speed ** 2 * 0.05

def f_power(speed, pitch):
    """power = speed^3 * 0.01  (ignores pitch for sensitivity tests)"""
    return speed ** 3 * 0.01

def f_Cm(speed, pitch):
    """Cm = cos(pitch_rad) - 0.01 * speed  (trim quantity, target ≈ 0)"""
    return np.cos(np.deg2rad(pitch)) - 0.01 * speed


# ─── Dataset builders ───────────────────────────────────────────────────────

def make_dataset(n: int = 30, seed: int = 42, add_nan: bool = False,
                 add_outlier: bool = False) -> pd.DataFrame:
    """Return a DataFrame with columns: speed, pitch, thrust, power, Cm."""
    rng = np.random.default_rng(seed)
    speed = rng.uniform(20, 100, n)
    pitch = rng.uniform(-10, 10, n)
    thrust = f_thrust(speed, pitch) + rng.normal(0, 0.1, n)
    power  = f_power(speed, pitch)  + rng.normal(0, 0.5, n)
    Cm     = f_Cm(speed, pitch)     + rng.normal(0, 0.005, n)
    df = pd.DataFrame({"speed": speed, "pitch": pitch,
                       "thrust": thrust, "power": power, "Cm": Cm})
    if add_nan:
        df.loc[0, "thrust"] = np.nan
    if add_outlier:
        df.loc[1, "power"] = 99999.0   # gross outlier
    return df


def make_simple_dataset(n: int = 20, seed: int = 0) -> pd.DataFrame:
    """2-input, 1-output dataset: y = x1^2 + x2  (known optimum at x1=1, x2=1)."""
    rng = np.random.default_rng(seed)
    x1 = rng.uniform(0, 1, n)
    x2 = rng.uniform(0, 1, n)
    y  = x1 ** 2 + x2 + rng.normal(0, 0.01, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


# ─── Pytest fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def aero_df():
    return make_dataset(n=30, seed=42)

@pytest.fixture
def aero_df_with_nan():
    return make_dataset(n=20, seed=1, add_nan=True)

@pytest.fixture
def aero_df_with_outlier():
    return make_dataset(n=20, seed=2, add_outlier=True)

@pytest.fixture
def simple_df():
    return make_simple_dataset(n=20, seed=0)

@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c

@pytest.fixture
def uploaded_job(client, aero_df, tmp_path):
    """Upload the aero dataset and return the job_id."""
    csv_bytes = aero_df.to_csv(index=False).encode()
    data = {"file": (csv_bytes, "test_aero.csv", "text/csv")}
    from io import BytesIO
    res = client.post("/upload", data={"file": (BytesIO(csv_bytes), "test.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 200
    return res.get_json()["job_id"]
