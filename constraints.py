# =============================================================================
# constraints.py
# Parametric Optimization Driver
# Version: v1.1.3
# Role: Security Engineer
# Last modified: 2026-05-06
# Description: Constraint evaluation for eq/leq/geq output constraints with
#              constant, expression (sandboxed eval with AST whitelist), or
#              table-interpolated limits. Computes GP-based feasibility
#              probabilities via normal CDF.
# =============================================================================

"""
Constraint evaluation: equality, leq, geq constraints with constant, expression,
or table-interpolated limits. Computes GP-based feasibility probabilities.
"""

import ast
import math
import tempfile
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

# SECURITY: Whitelist of allowed AST node types for constraint expressions.
# Approach: explicit whitelist (not blacklist). Any node type not in this set
# is rejected before eval() is called, blocking sandbox escapes via:
#   - ast.Attribute  → .__class__, .method, etc. (class-traversal attacks)
#   - ast.Subscript  → [key], [i:j]  (index-based builtins access)
#   - ast.Lambda     → lambda expressions
#   - ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp → comprehensions
#   - ast.JoinedStr  → f-strings
#   - ast.Starred    → *args unpacking
#   - Any async/generator node types
_ALLOWED_EXPR_NODES = frozenset({
    ast.Expression,
    # Literals
    ast.Constant,
    ast.Tuple,   # for np.interp(x, (x0, x1), (y0, y1))
    ast.List,    # for np.interp(x, [x0, x1], [y0, y1])
    # Name lookup (actual names checked against scope at runtime)
    ast.Name, ast.Load,
    # Arithmetic
    ast.BinOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    # Unary
    ast.UnaryOp, ast.UAdd, ast.USub, ast.Not,
    # Boolean
    ast.BoolOp, ast.And, ast.Or,
    # Comparison
    ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    # Function calls — function name must resolve in _SAFE_NUMPY at runtime
    ast.Call,
    ast.keyword,  # keyword args, e.g. clip(x, a_min=0, a_max=1)
})


def _validate_expression_ast(expr: str) -> None:
    """
    SECURITY: Parse the expression as an AST and reject any disallowed node type
    before eval() is called. This is the primary sandbox defense against
    class-hierarchy traversal (.__class__.__mro__), subscript-based builtins
    access (__builtins__['key']), lambda expressions, and comprehensions.

    Raises ValueError for disallowed nodes.
    Raises ValueError wrapping SyntaxError for unparseable expressions.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_EXPR_NODES:
            raise ValueError(
                f"Expression contains disallowed construct '{type(node).__name__}'. "
                "Only arithmetic, comparisons, and allowed numpy math functions are "
                "permitted. Attribute access, indexing, lambda, and comprehensions "
                "are not allowed."
            )


@dataclass
class ConstraintDef:
    """
    Definition of a single output constraint.

    col:                  Output column name to constrain.
    ctype:                "eq" | "leq" | "geq"
    target:               Target value for eq constraints (|output - target| <= tol).
    tolerance:            Tolerance band for eq constraints.
    limit_type:           "constant" | "expression" | "table"
    limit_value:          float (constant), str (expression or table path).
                          Not used for eq constraints (target/tolerance are the limit).
    table_condition_cols: Input column names used as interpolation keys in table mode.
    table_limit_col:      Column in the table CSV that contains the limit values.
    _interpolator:        Cached LinearNDInterpolator, populated on first table use.
    """
    col: str
    ctype: str          # "eq" | "leq" | "geq"
    target: float = 0.0
    tolerance: float = 0.001
    limit_type: str = "constant"   # "constant" | "expression" | "table"
    limit_value: Any = None        # float | str (expression) | str (table CSV path)
    table_condition_cols: list = field(default_factory=list)
    table_limit_col: str = ""

    # Cached interpolator for table limits (populated on first use)
    _interpolator: Any = field(default=None, repr=False, compare=False)


def _resolve_limit(constraint: ConstraintDef, row_vars: dict) -> float:
    """
    Compute the numeric limit value for the constraint given the current row's
    input variables. Not called for eq constraints (they use target/tolerance directly).
    """
    lt = constraint.limit_type

    if lt == "constant":
        return float(constraint.limit_value)

    elif lt == "expression":
        expr = constraint.limit_value
        # SECURITY: Validate AST before eval — blocks class traversal, subscript,
        # lambda, comprehensions, and any other disallowed node types.
        _validate_expression_ast(expr)
        scope = {**_SAFE_NUMPY, **row_vars}
        try:
            # SECURITY: __builtins__ is an empty dict (blocks all builtins).
            # Combined with the AST whitelist above, this is defense-in-depth.
            result = eval(expr, {"__builtins__": {}}, scope)  # noqa: S307
        except (NameError, KeyError) as exc:
            # KeyError in Python 3.14+ when injection tries __builtins__['key'].
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
            # Out of range — return NaN to signal the caller to handle gracefully
            return np.nan
        return float(result[0])

    else:
        raise ValueError(f"Unknown limit_type: {lt!r}")


def _build_interpolator(constraint: ConstraintDef) -> LinearNDInterpolator:
    """
    Build the scipy LinearNDInterpolator from the constraint table CSV.

    SECURITY: The table path is validated to be within the upload directory
    before opening. This prevents path traversal attacks where a crafted
    limit_value could read arbitrary files from the filesystem.
    """
    # SECURITY: Resolve the path and confirm it is inside the upload directory.
    upload_dir = (Path(tempfile.gettempdir()) / "cfd_opt_uploads").resolve()
    path = Path(constraint.limit_value).resolve()
    if not path.is_relative_to(upload_dir):
        raise ValueError(
            f"Constraint table path is outside the upload directory. "
            f"Only files uploaded via /upload_constraint_table are permitted."
        )
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
    Uses normal CDF. sigma is floored at 1e-9 to prevent division by zero.

    eq constraints use target/tolerance directly and do not call _resolve_limit,
    because limit_value is unused for equality checks.

    Return value is always in [0.0, 1.0]:
    - leq/geq: norm.cdf output is bounded [0, 1] by definition.
    - eq:  difference of two norm.cdf values with the larger subtracted from the smaller,
           so result is in [0, 1].
    """
    if sigma <= 0:
        sigma = 1e-9

    ctype = constraint.ctype

    if ctype == "eq":
        # eq uses target + tolerance directly — limit_value is not relevant.
        # P(|output - target| <= tol) = Φ((tol - (mu-target))/σ) - Φ((-tol - (mu-target))/σ)
        diff = mu - constraint.target
        tol = constraint.tolerance
        p = norm.cdf((tol - diff) / sigma) - norm.cdf((-tol - diff) / sigma)
        return float(p)

    limit = _resolve_limit(constraint, row_vars)
    if np.isnan(limit):
        return 0.5  # Out-of-range table — treat as 50% feasible, warn upstream

    if ctype == "leq":
        # P(output <= limit) = Φ((limit - μ) / σ)
        return float(norm.cdf((limit - mu) / sigma))

    elif ctype == "geq":
        # P(output >= limit) = 1 - Φ((limit - μ) / σ)
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
        # eq uses target + tolerance directly — limit_value is not relevant.
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
    Example: "chord * twist <= 15.0"

    SECURITY (defense-in-depth, two layers):
    1. AST whitelist (_validate_expression_ast): rejects disallowed node types
       before eval() runs. Blocks attribute access (.__class__), subscript ([]),
       lambda, comprehensions, f-strings, and starred expressions.
    2. Empty __builtins__ dict: blocks any remaining name-based builtins access
       (__import__, exec, open, globals, locals, etc.) via NameError at runtime.
       In Python 3.14+, subscript-based access raises KeyError, also caught here.
    """
    # SECURITY layer 1: AST whitelist — must pass before eval is attempted.
    _validate_expression_ast(expr)

    scope = {**_SAFE_NUMPY, **row_vars}
    try:
        # SECURITY layer 2: empty __builtins__ — blocks all builtin name lookups.
        result = eval(expr, {"__builtins__": {}}, scope)  # noqa: S307
    except (NameError, KeyError) as exc:
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
    When CEI uses this, a near-zero product from one tight constraint correctly
    drives CEI toward zero without any division (product is multiplicative only).
    """
    probs = []
    for c in constraints:
        mu = mu_dict.get(c.col, 0.0)
        sigma = sigma_dict.get(c.col, 1e-9)
        probs.append(p_feasible(mu, sigma, c, row_vars))
    product = float(np.prod(probs)) if probs else 1.0
    return product, probs
