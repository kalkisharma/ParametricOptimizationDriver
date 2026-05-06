# =============================================================================
# constraints.py
# Parametric Optimization Driver
# Version: v1.1.1
# Role: Security Engineer
# Last modified: 2026-05-06
# Description: Constraint evaluation for eq/leq/geq output constraints with
#              constant, expression (sandboxed eval), or table-interpolated limits.
#              Computes GP-based feasibility probabilities via normal CDF.
# =============================================================================

"""
Constraint evaluation: equality, leq, geq constraints with constant, expression,
or table-interpolated limits. Computes GP-based feasibility probabilities.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator
from scipy.stats import norm

# Safe numpy functions available in constraint expressions
_SAFE_NUMPY = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "atan2": np.arctan2,
    "exp": np.exp, "log": np.log, "log10": np.log10,
    "sqrt": np.sqrt, "abs": np.abs, "clip": np.clip,
    "interp": np.interp,
    "pi": math.pi, "e": math.e,
    "min": min, "max": max,
}


@dataclass
class ConstraintDef:
    col: str
    ctype: str          # "eq" | "leq" | "geq"
    target: float = 0.0
    tolerance: float = 0.001
    limit_type: str = "constant"   # "constant" | "expression" | "table"
    limit_value: Any = None        # float | str (expression) | str (table CSV path)
    table_condition_cols: list = field(default_factory=list)  # for table lookup
    table_limit_col: str = ""

    # Cached interpolator for table limits (populated on first use)
    _interpolator: Any = field(default=None, repr=False, compare=False)


def _resolve_limit(constraint: ConstraintDef, row_vars: dict) -> float:
    """
    Compute the numeric limit value for the constraint given the current row's variables.
    """
    lt = constraint.limit_type

    if lt == "constant":
        return float(constraint.limit_value)

    elif lt == "expression":
        expr = constraint.limit_value
        scope = {**_SAFE_NUMPY, **row_vars}
        try:
            # SECURITY: __builtins__ is set to an empty dict to block all builtins.
            # Only _SAFE_NUMPY functions and row variable names are in scope.
            result = eval(expr, {"__builtins__": {}}, scope)  # noqa: S307
        except (NameError, KeyError) as exc:
            # KeyError occurs in Python 3.14+ when code tries __builtins__['key']
            # on the empty builtins dict — treat as equivalent to NameError.
            raise NameError(
                f"Constraint expression references an undefined name: {exc}. "
                f"Only numpy math functions and input variable names are allowed."
            ) from exc
        except Exception as exc:
            raise ValueError(f"Constraint expression evaluation error: {exc}") from exc
        return float(result)

    elif lt == "table":
        if constraint._interpolator is None:
            constraint._interpolator = _build_interpolator(constraint)
        cond_vals = np.array([[row_vars[c] for c in constraint.table_condition_cols]])
        result = constraint._interpolator(cond_vals)
        if np.isnan(result[0]):
            # Out of range — return a very permissive limit and warn via return value
            return np.nan
        return float(result[0])

    else:
        raise ValueError(f"Unknown limit_type: {lt!r}")


def _build_interpolator(constraint: ConstraintDef) -> LinearNDInterpolator:
    """Build the scipy interpolator from the table CSV."""
    path = Path(constraint.limit_value)
    if not path.exists():
        raise FileNotFoundError(f"Constraint table not found: {path}")
    df = pd.read_csv(path)
    points = df[constraint.table_condition_cols].values
    values = df[constraint.table_limit_col].values
    return LinearNDInterpolator(points, values)


def p_feasible(
    mu: float, sigma: float, constraint: ConstraintDef, row_vars: dict
) -> float:
    """
    Probability that the GP output satisfies the constraint, given GP prediction (mu, sigma).
    Uses normal CDF with sigma floored at 1e-9 to prevent division by zero.

    eq constraints use target/tolerance directly and do not call _resolve_limit,
    because limit_value is unused for equality checks.
    """
    if sigma <= 0:
        sigma = 1e-9

    ctype = constraint.ctype

    if ctype == "eq":
        # eq uses target + tolerance directly — limit_value is not relevant
        # P(|output - target| <= tol) = Φ((tol - (mu-target))/sigma) - Φ((-tol - (mu-target))/sigma)
        diff = mu - constraint.target
        tol = constraint.tolerance
        p = norm.cdf((tol - diff) / sigma) - norm.cdf((-tol - diff) / sigma)
        return float(p)

    limit = _resolve_limit(constraint, row_vars)
    if np.isnan(limit):
        return 0.5  # Out-of-range table — treat as 50% feasible, warn upstream

    if ctype == "leq":
        # P(output <= limit) = Φ((limit - mu) / sigma)
        return float(norm.cdf((limit - mu) / sigma))

    elif ctype == "geq":
        # P(output >= limit) = 1 - Φ((limit - mu) / sigma)
        return float(1 - norm.cdf((limit - mu) / sigma))

    else:
        raise ValueError(f"Unknown constraint type: {ctype!r}")


def evaluate_deterministic(
    row_vals: dict, constraint: ConstraintDef
) -> tuple[bool, float]:
    """
    Evaluate constraint on a known output value (no GP uncertainty).
    Returns (satisfied: bool, margin: float).

    eq constraints use target/tolerance directly and skip _resolve_limit because
    limit_value is not meaningful for equality checks (the target IS the limit).
    """
    output_val = row_vals[constraint.col]

    if constraint.ctype == "eq":
        # eq uses target + tolerance directly — limit_value is not relevant
        margin = constraint.tolerance - abs(output_val - constraint.target)
        return abs(output_val - constraint.target) <= constraint.tolerance, margin

    limit = _resolve_limit(constraint, row_vals)
    if np.isnan(limit):
        return False, float("nan")

    if constraint.ctype == "leq":
        margin = limit - output_val
        return output_val <= limit, margin
    elif constraint.ctype == "geq":
        margin = output_val - limit
        return output_val >= limit, margin
    else:
        raise ValueError(f"Unknown constraint type: {constraint.ctype!r}")


def evaluate_input_constraint(expr: str, row_vars: dict) -> bool:
    """
    Evaluate an input-space constraint expression (returns bool).
    expr example: "chord * twist <= 15.0"

    SECURITY: Only _SAFE_NUMPY functions and input variable names are in scope.
    __builtins__ is empty to block imports, exec, open, and all other builtins.
    KeyError is caught alongside NameError because Python 3.14+ raises KeyError
    when code indexes into the empty __builtins__ dict (e.g. __builtins__['__import__']).
    """
    scope = {**_SAFE_NUMPY, **row_vars}
    try:
        # SECURITY: __builtins__ is set to an empty dict to block all builtins.
        result = eval(expr, {"__builtins__": {}}, scope)  # noqa: S307
    except (NameError, KeyError) as exc:
        # KeyError occurs in Python 3.14+ when an injection tries __builtins__['key'].
        raise NameError(
            f"Input constraint references an undefined name: {exc}. "
            "Only numpy math functions and input variable names are allowed."
        ) from exc
    return bool(result)


def all_p_feasible(
    mu_dict: dict,
    sigma_dict: dict,
    constraints: list[ConstraintDef],
    row_vars: dict,
) -> tuple[float, list[float]]:
    """
    Compute product of feasibility probabilities across all output constraints.
    Returns (product, [p_i for each constraint]).
    """
    probs = []
    for c in constraints:
        mu = mu_dict.get(c.col, 0.0)
        sigma = sigma_dict.get(c.col, 1e-9)
        probs.append(p_feasible(mu, sigma, c, row_vars))
    product = float(np.prod(probs)) if probs else 1.0
    return product, probs
