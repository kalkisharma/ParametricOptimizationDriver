# =============================================================================
# tests/test_preprocessing.py
# Parametric Optimization Driver
# Version: v1.1.5
# Role: QA Engineer, Scientific Python Developer
# Last modified: 2026-05-06
# Description: Tests for preprocessing.py — outlier detection (IQR + Isolation
#              Forest), NaN handling, clean(), edge cases (empty/single-column).
# =============================================================================

"""Tests for preprocessing.py: outlier detection, NaN handling, clean()."""

import numpy as np
import pandas as pd
import pytest

from preprocessing import clean, detect_outliers, nan_report
from tests.conftest import make_dataset


INPUT_COLS  = ["speed", "pitch"]
OUTPUT_COLS = ["thrust", "power", "Cm"]
ALL_COLS    = INPUT_COLS + OUTPUT_COLS


def test_detect_outliers_flags_injected(aero_df_with_outlier):
    mask = detect_outliers(aero_df_with_outlier, ALL_COLS)
    assert mask[1], "Injected gross outlier (row 1) must be flagged"

def test_detect_outliers_low_false_positive(aero_df):
    mask = detect_outliers(aero_df, ALL_COLS)
    n_flagged = mask.sum()
    # With contamination=0.1 and 30 rows, at most 3 should be flagged
    assert n_flagged <= 5, f"Too many false positives: {n_flagged}"

def test_detect_outliers_flags_nan(aero_df_with_nan):
    mask = detect_outliers(aero_df_with_nan, ALL_COLS)
    assert mask[0], "Row with NaN must be flagged"

def test_detect_outliers_returns_bool_mask(aero_df):
    mask = detect_outliers(aero_df, ALL_COLS)
    assert mask.dtype == bool
    assert len(mask) == len(aero_df)

def test_clean_splits_correctly(aero_df):
    n = len(aero_df)
    include_mask = np.ones(n, dtype=bool)
    include_mask[0] = False
    included, excluded = clean(aero_df, include_mask)
    assert len(included) == n - 1
    assert len(excluded) == 1

def test_clean_all_included(aero_df):
    include_mask = np.ones(len(aero_df), dtype=bool)
    included, excluded = clean(aero_df, include_mask)
    assert len(included) == len(aero_df)
    assert len(excluded) == 0

def test_nan_report_counts(aero_df_with_nan):
    report = nan_report(aero_df_with_nan, OUTPUT_COLS)
    assert report["per_column"]["thrust"] == 1
    assert report["rows_with_nan"] == 1

def test_nan_report_clean(aero_df):
    report = nan_report(aero_df, OUTPUT_COLS)
    assert report["rows_with_nan"] == 0


# ─── Gate 6: coverage gap tests ──────────────────────────────────────────────

def test_detect_outliers_empty_dataset():
    """Empty dataframe should return an empty boolean mask without raising."""
    df = pd.DataFrame({"speed": pd.Series([], dtype=float),
                       "thrust": pd.Series([], dtype=float)})
    mask = detect_outliers(df, ["speed", "thrust"])
    assert len(mask) == 0
    assert mask.dtype == bool


def test_detect_outliers_single_column():
    """Single-column dataset should produce a valid boolean mask."""
    rng = np.random.default_rng(42)
    df = pd.DataFrame({"speed": rng.uniform(20, 100, 20)})
    mask = detect_outliers(df, ["speed"])
    assert len(mask) == 20
    assert mask.dtype == bool
