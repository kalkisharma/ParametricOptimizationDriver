# =============================================================================
# sensitivity.py
# Parametric Optimization Driver
# Version: v1.1.2
# Role: Scientific Python Developer
# Last modified: 2026-05-06
# Description: First-order Sobol sensitivity indices via GP-surrogate Monte Carlo
#              using the Saltelli (2002) estimator. Returns per-output S1 dicts
#              and a Plotly bar chart JSON.
# =============================================================================

"""
First-order Sobol sensitivity indices via GP Monte Carlo (Saltelli method).
Returns Plotly bar chart JSON per output column.
"""

import json

import numpy as np
import plotly.graph_objects as go

from surrogate import SurrogateModel


def sobol_first_order(
    surrogate: SurrogateModel,
    bounds: list[tuple[float, float]],
    output_col: str,
    n_samples: int = 1024,
    seed: int = 42,
) -> dict[str, float]:
    """
    Estimate first-order Sobol sensitivity indices S1 for one output column.
    Uses the Saltelli (2002) estimator with n_samples base samples.
    Returns {input_col: S1_value}.

    Sample size guidance: n_samples=1024 is adequate for 2–5 inputs on a
    well-fitted GP surrogate. For 10+ inputs, variance of the estimator
    increases and n_samples ≥ 4096 is recommended. The negative-value clip
    (max(0, S1_raw)) can bias the sum above 1.0 with small samples; this is
    expected and not an algorithmic error.
    """
    rng = np.random.default_rng(seed)
    n_inputs = len(bounds)
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])

    # Draw two independent sample matrices A and B, shape (n_samples, n_inputs)
    A = rng.uniform(lo, hi, size=(n_samples, n_inputs))
    B = rng.uniform(lo, hi, size=(n_samples, n_inputs))

    # f_A, f_B: GP mean predictions
    f_A = surrogate.predict(A)
    f_B = surrogate.predict(B)

    # Find column index
    col_idx = surrogate.output_cols.index(output_col)
    yA = f_A[:, col_idx]
    yB = f_B[:, col_idx]

    var_total = np.var(np.concatenate([yA, yB]))
    if var_total < 1e-12:
        return {col: 0.0 for col in surrogate.input_cols}

    s1 = {}
    for i, col in enumerate(surrogate.input_cols):
        # A_B(i): matrix A with column i replaced by B's column i
        AB_i = A.copy()
        AB_i[:, i] = B[:, i]
        f_AB_i = surrogate.predict(AB_i)[:, col_idx]

        # Saltelli estimator: S1_i = (1/n) * sum(f_B * (f_AB_i - f_A)) / Var
        s1_raw = float(np.mean(yB * (f_AB_i - yA)) / var_total)
        s1[col] = max(0.0, s1_raw)  # clip negative values (estimation noise)

    return s1


def sobol_chart_json(
    surrogate: SurrogateModel,
    bounds: list[tuple[float, float]],
    n_samples: int = 1024,
) -> dict:
    """
    Compute Sobol S1 for all output columns and return a Plotly bar chart JSON
    (one trace per output, grouped by input).
    """
    input_cols = surrogate.input_cols
    output_cols = surrogate.output_cols

    traces = []
    for out_col in output_cols:
        s1 = sobol_first_order(surrogate, bounds, out_col, n_samples)
        traces.append(
            go.Bar(
                name=out_col,
                x=input_cols,
                y=[s1.get(c, 0.0) for c in input_cols],
                text=[f"{s1.get(c, 0.0):.3f}" for c in input_cols],
                textposition="outside",
            )
        )

    fig = go.Figure(data=traces)
    fig.update_layout(
        barmode="group",
        xaxis_title="Input variable",
        yaxis_title="First-order Sobol index S₁",
        yaxis_range=[0, 1.05],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e4e4f0",
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=50, r=20, t=40, b=50),
        height=280,
        hoverlabel=dict(bgcolor="#1e1e3a", font=dict(color="#e4e4f0", size=12), bordercolor="#4a4a7a"),
    )
    fig.update_xaxes(gridcolor="#2e2e52")
    fig.update_yaxes(gridcolor="#2e2e52")

    return json.loads(fig.to_json())
