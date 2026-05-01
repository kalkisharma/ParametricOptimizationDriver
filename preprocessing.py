"""
Data preprocessing: outlier detection (IQR + Isolation Forest), NaN handling,
and Plotly parallel-coordinates chart JSON.
"""

import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.ensemble import IsolationForest


def detect_outliers(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """
    Return a boolean mask (True = outlier) for rows in `df` using the union of:
    - IQR rule: value outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR] in any column
    - Isolation Forest: multivariate anomaly detection

    Only considers the subset of columns in `cols`.
    Rows with all-NaN in `cols` are marked as outliers.
    """
    data = df[cols].copy()
    n = len(data)
    mask_iqr = np.zeros(n, dtype=bool)
    mask_iso = np.zeros(n, dtype=bool)

    # IQR per column
    for col in cols:
        s = data[col]
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            mask_iqr |= (s < lo) | (s > hi)

    # Isolation Forest on rows that have no NaN
    complete = data.dropna()
    if len(complete) >= 4:
        iso = IsolationForest(contamination=0.1, random_state=42, n_estimators=100)
        preds = iso.fit_predict(complete.values)
        iso_outlier_idx = complete.index[preds == -1]
        mask_iso[iso_outlier_idx] = True

    # NaN rows are always excluded
    nan_mask = data.isna().any(axis=1).values

    return mask_iqr | mask_iso | nan_mask


def clean(df: pd.DataFrame, include_mask: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split df into (included_df, excluded_df) based on include_mask (True = keep).
    Returns copies with reset indices.
    """
    included = df[include_mask].copy().reset_index(drop=True)
    excluded = df[~include_mask].copy().reset_index(drop=True)
    return included, excluded


def parallel_coords_chart(
    df: pd.DataFrame,
    cols: list[str],
    outlier_mask: np.ndarray,
) -> dict:
    """
    Build a Plotly parallel-coordinates chart JSON.
    Outlier rows are colored orange; clean rows are colored according to the
    first column's value (blue gradient).
    """
    data = df[cols].copy()

    # Color axis: 0 = clean, 1 = outlier
    color_vals = outlier_mask.astype(float)

    dimensions = []
    for col in cols:
        series = data[col].fillna(data[col].median())
        dimensions.append(
            dict(
                label=col,
                values=series.tolist(),
                range=[float(series.min()), float(series.max())],
            )
        )

    fig = go.Figure(
        go.Parcoords(
            line=dict(
                color=color_vals,
                colorscale=[[0, "#5b8ef7"], [1, "#f0b429"]],
                showscale=True,
                colorbar=dict(
                    title="Outlier",
                    tickvals=[0, 1],
                    ticktext=["Clean", "Flagged"],
                    thickness=12,
                    len=0.6,
                ),
            ),
            dimensions=dimensions,
            labelfont=dict(color="#9090b8", size=11),
            tickfont=dict(color="#9090b8", size=10),
        )
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e4e4f0",
        margin=dict(l=80, r=60, t=20, b=20),
        height=320,
    )

    return json.loads(fig.to_json())


def nan_report(df: pd.DataFrame, output_cols: list[str]) -> dict:
    """Return per-column NaN count and total rows dropped."""
    nan_counts = {col: int(df[col].isna().sum()) for col in output_cols}
    rows_with_nan = int(df[output_cols].isna().any(axis=1).sum())
    return {"per_column": nan_counts, "rows_with_nan": rows_with_nan}
