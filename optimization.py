"""
Optimization pipeline orchestration.

run_refinement  — surrogate refinement mode (max variance, space-filling)
run_optimization — constrained Bayesian optimization (CEI)

Both functions:
- Accept a pandas DataFrame and a config dict
- Emit SSE progress messages via an emit() callback
- Return a result dict with suggestions, plots, diagnostics, convergence info
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from acquisition import (
    ConstrainedEIAcquisition,
    FeasibilitySearchAcquisition,
    MaxVarianceAcquisition,
)
from constraints import ConstraintDef, all_p_feasible, evaluate_deterministic
from preprocessing import clean, detect_outliers, nan_report, parallel_coords_chart
from sensitivity import sobol_chart_json
from surrogate import SurrogateModel


# ---------------------------------------------------------------------------
# Config parsing helpers
# ---------------------------------------------------------------------------

def _parse_bounds(config: dict, input_cols: list[str], df: pd.DataFrame) -> list[tuple]:
    bounds_cfg = config.get("bounds", {})
    bounds = []
    for col in input_cols:
        lo = bounds_cfg.get(col, {}).get("min", float(df[col].min()))
        hi = bounds_cfg.get(col, {}).get("max", float(df[col].max()))
        if lo >= hi:
            hi = lo + 1.0
        bounds.append((lo, hi))
    return bounds


def _parse_integer_dims(config: dict, input_cols: list[str]) -> list[int]:
    integer_cols = set(config.get("integer_cols", []))
    return [i for i, col in enumerate(input_cols) if col in integer_cols]


def _parse_constraints(config: dict) -> list[ConstraintDef]:
    raw = config.get("constraints", [])
    result = []
    for c in raw:
        result.append(ConstraintDef(
            col=c["col"],
            ctype=c["type"],
            target=float(c.get("target", 0.0)),
            tolerance=float(c.get("tolerance", 0.001)),
            limit_type=c.get("limit_type", "constant"),
            limit_value=c.get("limit_value"),
            table_condition_cols=c.get("table_condition_cols", []),
            table_limit_col=c.get("table_limit_col", ""),
        ))
    return result


def _parse_gp_settings(config: dict) -> dict:
    gp = config.get("gp_settings", {})
    return {
        "kernel": gp.get("kernel", "auto"),
        "n_restarts": int(gp.get("n_restarts", 5)),
        "anisotropic": gp.get("length_scale_type", "anisotropic") == "anisotropic",
    }


# ---------------------------------------------------------------------------
# Shared preprocessing step
# ---------------------------------------------------------------------------

def _preprocess(
    df: pd.DataFrame,
    input_cols: list[str],
    output_cols: list[str],
    outlier_include_mask: list[bool] | None,
    emit: Callable,
) -> tuple[pd.DataFrame, dict]:
    """
    Drop NaN rows, apply user outlier mask, return cleaned df and nan_report.
    """
    emit("progress", "Cleaning data and applying outlier exclusions…", step=1, total=6)

    all_cols = input_cols + output_cols
    nan_info = nan_report(df, output_cols)

    # Drop rows with NaN in any used column
    df_clean = df.dropna(subset=all_cols).reset_index(drop=True)

    # Apply user outlier mask (True = include)
    if outlier_include_mask is not None and len(outlier_include_mask) == len(df_clean):
        mask = np.array(outlier_include_mask, dtype=bool)
        df_clean = df_clean[mask].reset_index(drop=True)

    return df_clean, nan_info


# ---------------------------------------------------------------------------
# Surrogate fitting
# ---------------------------------------------------------------------------

def _fit_surrogate(
    df: pd.DataFrame,
    input_cols: list[str],
    output_cols: list[str],
    gp_settings: dict,
    emit: Callable,
    total_steps: int = 6,
) -> SurrogateModel:
    emit("progress", f"Fitting surrogate (0/{len(output_cols)} outputs)…", step=2, total=total_steps)

    X = df[input_cols].values.astype(float)
    Y = df[output_cols].values.astype(float)

    surrogate = SurrogateModel(
        kernel=gp_settings["kernel"],
        n_restarts=gp_settings["n_restarts"],
        anisotropic=gp_settings["anisotropic"],
    )

    # Fit per-output, emitting progress
    for i, col in enumerate(output_cols):
        emit("progress", f"Fitting GP for '{col}' ({i+1}/{len(output_cols)})…",
             step=2, total=total_steps)

    surrogate.fit(X, Y, input_cols, output_cols)
    return surrogate


# ---------------------------------------------------------------------------
# Suggestions → output records
# ---------------------------------------------------------------------------

def _build_suggestion_records(
    suggested_X: np.ndarray,
    surrogate: SurrogateModel,
    constraints: list[ConstraintDef],
    input_cols: list[str],
    output_cols: list[str],
    objective_spec: dict,
) -> list[dict]:
    means, stds = surrogate.predict_with_std(suggested_X)
    records = []
    ts = datetime.now(timezone.utc).isoformat()

    for i, x_row in enumerate(suggested_X):
        row: dict = {}
        for j, col in enumerate(input_cols):
            row[col] = round(float(x_row[j]), 6)

        for j, col in enumerate(output_cols):
            mu = float(means[i, j])
            sigma = float(stds[i, j])
            row[f"pred_{col}"] = round(mu, 6)
            row[f"pred_{col}_lower"] = round(mu - 2 * sigma, 6)
            row[f"pred_{col}_upper"] = round(mu + 2 * sigma, 6)

        mu_dict = {col: float(means[i, j]) for j, col in enumerate(output_cols)}
        sigma_dict = {col: float(stds[i, j]) for j, col in enumerate(output_cols)}
        row_vars = {col: float(x_row[j]) for j, col in enumerate(input_cols)}

        _, probs = all_p_feasible(mu_dict, sigma_dict, constraints, row_vars)
        for k, c in enumerate(constraints):
            row[f"p_feasible_c{k+1}_{c.col}"] = round(probs[k], 4)

        row["timestamp"] = ts
        records.append(row)

    return records


# ---------------------------------------------------------------------------
# Plot builders
# ---------------------------------------------------------------------------

def _scatter_matrix_json(df: pd.DataFrame, input_cols: list[str], output_cols: list[str]) -> dict:
    all_cols = input_cols + output_cols
    cols_to_plot = all_cols[:min(len(all_cols), 8)]  # cap for readability
    n = len(cols_to_plot)
    fig = px.scatter_matrix(
        df[cols_to_plot],
        dimensions=cols_to_plot,
        color_discrete_sequence=["#5b8ef7"],
        template="plotly",
    )
    fig.update_traces(diagonal_visible=False, showupperhalf=False, marker_size=6)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4f0", size=11),
        height=max(500, n * 120),
        margin=dict(l=80, r=20, t=40, b=80),
        hoverlabel=dict(bgcolor="#1e1e3a", font=dict(color="#e4e4f0", size=12), bordercolor="#4a4a7a"),
    )
    return json.loads(fig.to_json())


def _uncertainty_heatmap_json(
    surrogate: SurrogateModel,
    bounds: list[tuple],
    x_col: str,
    y_col: str,
    n_grid: int = 40,
) -> dict:
    input_cols = surrogate.input_cols
    xi = input_cols.index(x_col)
    yi = input_cols.index(y_col)

    bx = bounds[xi]
    by = bounds[yi]
    xs = np.linspace(bx[0], bx[1], n_grid)
    ys = np.linspace(by[0], by[1], n_grid)

    # Fix all other inputs at their midpoint
    x_mid = [(lo + hi) / 2 for lo, hi in bounds]
    grid_X = []
    for yv in ys:
        for xv in xs:
            row = x_mid.copy()
            row[xi] = xv
            row[yi] = yv
            grid_X.append(row)

    grid_X = np.array(grid_X)
    _, stds = surrogate.predict_with_std(grid_X)
    total_std = stds.sum(axis=1).reshape(n_grid, n_grid)

    fig = go.Figure(go.Heatmap(
        x=xs.tolist(),
        y=ys.tolist(),
        z=total_std.tolist(),
        colorscale="Plasma",
        colorbar=dict(title="Σ σ", thickness=12),
    ))
    fig.update_layout(
        xaxis_title=x_col,
        yaxis_title=y_col,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e4e4f0",
        height=360,
        margin=dict(l=60, r=20, t=20, b=50),
        hoverlabel=dict(bgcolor="#1e1e3a", font=dict(color="#e4e4f0", size=12), bordercolor="#4a4a7a"),
    )
    return json.loads(fig.to_json())


def _convergence_chart_json(
    df: pd.DataFrame,
    output_cols: list[str],
    input_cols: list[str],
    objective_spec: dict,
    constraints: list[ConstraintDef],
) -> dict:
    """Plot best feasible objective value vs. row index."""
    obj_col = objective_spec.get("column")
    direction = objective_spec.get("direction", "maximize")
    sign = 1.0 if direction == "maximize" else -1.0

    if obj_col in input_cols:
        obj_vals = df[obj_col].values
    elif obj_col in output_cols:
        obj_vals = df[obj_col].values
    else:
        weights = objective_spec.get("weights", {})
        obj_vals = sum(
            w * df[c].values for c, w in weights.items() if c in df.columns
        )

    # Feasibility mask from data
    feasible = np.ones(len(df), dtype=bool)
    for c in constraints:
        if c.col not in df.columns:
            continue
        for i, row in df.iterrows():
            row_vars = {col: row[col] for col in df.columns}
            sat, _ = evaluate_deterministic(row_vars, c)
            if not sat:
                feasible[i] = False

    best_so_far = []
    current_best = -np.inf
    for i in range(len(df)):
        if feasible[i]:
            val = sign * obj_vals[i]
            current_best = max(current_best, val)
        best_so_far.append(current_best if current_best > -np.inf else None)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(len(df))),
        y=[sign * v if v is not None else None for v in best_so_far],
        mode="lines+markers",
        name="Best feasible",
        line=dict(color="#5b8ef7", width=2),
        marker=dict(size=5),
    ))
    fig.update_layout(
        xaxis_title="Row index",
        yaxis_title=f"Best feasible {obj_col}",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e4e4f0",
        height=260,
        margin=dict(l=60, r=20, t=20, b=50),
        hoverlabel=dict(bgcolor="#1e1e3a", font=dict(color="#e4e4f0", size=12), bordercolor="#4a4a7a"),
    )
    fig.update_xaxes(gridcolor="#2e2e52")
    fig.update_yaxes(gridcolor="#2e2e52")
    return json.loads(fig.to_json())


# ---------------------------------------------------------------------------
# Latin Hypercube cold start
# ---------------------------------------------------------------------------

def _lhs_design(bounds: list[tuple], n: int, integer_dims: list[int]) -> np.ndarray:
    from scipy.stats.qmc import LatinHypercube, scale
    sampler = LatinHypercube(d=len(bounds), seed=42)
    sample = sampler.random(n=n)
    lo = [b[0] for b in bounds]
    hi = [b[1] for b in bounds]
    X = scale(sample, lo, hi)
    for d in integer_dims:
        X[:, d] = np.round(X[:, d])
    return X


# ---------------------------------------------------------------------------
# run_refinement
# ---------------------------------------------------------------------------

def run_refinement(df: pd.DataFrame, config: dict, emit: Callable) -> dict:
    input_cols: list[str] = config["input_cols"]
    output_cols: list[str] = config["output_cols"]
    n_suggestions: int = int(config.get("n_suggestions", 5))
    outlier_mask = config.get("outlier_include_mask")
    gp_settings = _parse_gp_settings(config)
    bounds = _parse_bounds(config, input_cols, df)
    integer_dims = _parse_integer_dims(config, input_cols)
    dup_threshold = float(config.get("dup_threshold", 0.01))

    # Cold start — no output data
    if df[output_cols].isna().all().all() or len(df) == 0:
        emit("progress", "No output data — generating LHS initial design…", step=1, total=2)
        X_lhs = _lhs_design(bounds, n_suggestions, integer_dims)
        suggestions = [
            {**{col: round(float(X_lhs[i, j]), 6) for j, col in enumerate(input_cols)},
             "timestamp": datetime.now(timezone.utc).isoformat()}
            for i in range(n_suggestions)
        ]
        emit("progress", "LHS design ready.", step=2, total=2)
        return {"suggestions": suggestions, "plots": {}, "diagnostics": {}, "mode": "cold_start"}

    df_clean, nan_info = _preprocess(df, input_cols, output_cols, outlier_mask, emit)
    min_rows = len(input_cols) + 1
    if len(df_clean) < min_rows:
        raise ValueError(
            f"Not enough data after cleaning: {len(df_clean)} rows (need at least {min_rows})."
        )

    surrogate = _fit_surrogate(df_clean, input_cols, output_cols, gp_settings, emit)

    emit("progress", "Computing sensitivity indices…", step=3, total=6)
    sobol_chart = sobol_chart_json(surrogate, bounds)

    emit("progress", "Running MaxVariance acquisition…", step=4, total=6)
    X_train = df_clean[input_cols].values.astype(float)
    Y_train = df_clean[output_cols].values.astype(float)

    strategy = MaxVarianceAcquisition()
    suggested_X = strategy.suggest(
        surrogate, bounds, n_suggestions, X_train,
        integer_dims=integer_dims,
        dup_threshold=dup_threshold,
        X_train=X_train,
        Y_train=Y_train,
    )

    suggestions = _build_suggestion_records(suggested_X, surrogate, [], input_cols, output_cols, {})

    emit("progress", "Building charts…", step=5, total=6)
    x_ax = input_cols[0]
    y_ax = input_cols[1] if len(input_cols) > 1 else input_cols[0]
    plots = {
        "scatter_matrix": _scatter_matrix_json(df_clean, input_cols, output_cols),
        "uncertainty_map": _uncertainty_heatmap_json(surrogate, bounds, x_ax, y_ax),
        "sensitivity": sobol_chart,
    }

    emit("progress", "Done.", step=6, total=6)
    return {
        "suggestions": suggestions,
        "plots": plots,
        "diagnostics": surrogate.loo_diagnostics(),
        "nan_info": nan_info,
        "mode": "refinement",
        "convergence": None,
        "unc_axes": {"x": x_ax, "y": y_ax},
        "_surrogate": surrogate,
    }


# ---------------------------------------------------------------------------
# run_optimization
# ---------------------------------------------------------------------------

def run_optimization(df: pd.DataFrame, config: dict, emit: Callable) -> dict:
    input_cols: list[str] = config["input_cols"]
    output_cols: list[str] = config["output_cols"]
    n_suggestions: int = int(config.get("n_suggestions", 5))
    outlier_mask = config.get("outlier_include_mask")
    gp_settings = _parse_gp_settings(config)
    bounds = _parse_bounds(config, input_cols, df)
    integer_dims = _parse_integer_dims(config, input_cols)
    dup_threshold = float(config.get("dup_threshold", 0.01))
    convergence_threshold = float(config.get("convergence_threshold", 0.01))
    objective_spec = config.get("objective_spec", {})
    constraints = _parse_constraints(config)
    input_constraint_exprs: list[str] = config.get("input_constraints", [])

    # Cold start
    if df[output_cols].isna().all().all() or len(df) == 0:
        emit("progress", "No output data — generating LHS initial design…", step=1, total=2)
        X_lhs = _lhs_design(bounds, n_suggestions, integer_dims)
        suggestions = [
            {**{col: round(float(X_lhs[i, j]), 6) for j, col in enumerate(input_cols)},
             "timestamp": datetime.now(timezone.utc).isoformat()}
            for i in range(n_suggestions)
        ]
        emit("progress", "LHS design ready.", step=2, total=2)
        return {"suggestions": suggestions, "plots": {}, "diagnostics": {}, "mode": "cold_start"}

    df_clean, nan_info = _preprocess(df, input_cols, output_cols, outlier_mask, emit)
    min_rows = len(input_cols) + 1
    if len(df_clean) < min_rows:
        raise ValueError(
            f"Not enough data after cleaning: {len(df_clean)} rows (need at least {min_rows})."
        )

    surrogate = _fit_surrogate(df_clean, input_cols, output_cols, gp_settings, emit)

    emit("progress", "Computing sensitivity indices…", step=3, total=6)
    sobol_chart = sobol_chart_json(surrogate, bounds)

    X_train = df_clean[input_cols].values.astype(float)
    Y_train = df_clean[output_cols].values.astype(float)

    # Determine if any feasible point exists
    strategy_cls = ConstrainedEIAcquisition
    cei_strategy = ConstrainedEIAcquisition()

    emit("progress", "Running acquisition optimizer…", step=4, total=6)
    suggested_X = cei_strategy.suggest(
        surrogate, bounds, n_suggestions, X_train,
        constraints=constraints,
        input_constraints=input_constraint_exprs,
        integer_dims=integer_dims,
        dup_threshold=dup_threshold,
        X_train=X_train,
        Y_train=Y_train,
        objective_spec=objective_spec,
    )

    # Detect feasibility mode (was FeasibilitySearch used?)
    best_f, feasible_mask = cei_strategy._best_feasible(
        X_train, Y_train, constraints, input_cols, output_cols, objective_spec
    )
    feasibility_mode = not feasible_mask.any()

    # Convergence check
    max_cei = 0.0
    converged = False
    if not feasibility_mode:
        emit("progress", "Checking convergence…", step=4, total=6)
        max_cei = cei_strategy.max_cei_value(
            surrogate, bounds, X_train, Y_train, constraints, objective_spec
        )
        converged = max_cei < convergence_threshold

    suggestions = _build_suggestion_records(
        suggested_X, surrogate, constraints, input_cols, output_cols, objective_spec
    )

    emit("progress", "Building charts…", step=5, total=6)
    x_ax = input_cols[0]
    y_ax = input_cols[1] if len(input_cols) > 1 else input_cols[0]
    plots = {
        "scatter_matrix": _scatter_matrix_json(df_clean, input_cols, output_cols),
        "uncertainty_map": _uncertainty_heatmap_json(surrogate, bounds, x_ax, y_ax),
        "sensitivity": sobol_chart,
        "convergence": _convergence_chart_json(
            df_clean, output_cols, input_cols, objective_spec, constraints
        ),
    }

    emit("progress", "Done.", step=6, total=6)
    return {
        "suggestions": suggestions,
        "plots": plots,
        "diagnostics": surrogate.loo_diagnostics(),
        "nan_info": nan_info,
        "mode": "optimization",
        "feasibility_mode": feasibility_mode,
        "convergence": {
            "max_cei": round(max_cei, 6),
            "threshold": convergence_threshold,
            "converged": converged,
        },
        "unc_axes": {"x": x_ax, "y": y_ax},
        "_surrogate": surrogate,
    }
