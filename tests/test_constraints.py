# =============================================================================
# tests/test_constraints.py
# Parametric Optimization Driver
# Version: v1.1.3
# Role: QA Engineer, Security Engineer
# Last modified: 2026-05-06
# Description: Tests for constraints.py — all limit types, feasibility
#              probabilities, and security (AST sandbox, path traversal).
# =============================================================================

"""Tests for constraints.py: all limit types, feasibility probabilities, security."""

import math
import numpy as np
import pytest

from constraints import (
    ConstraintDef,
    all_p_feasible,
    evaluate_deterministic,
    evaluate_input_constraint,
    p_feasible,
)


# ─── ConstraintDef construction ─────────────────────────────────────────────

def make_leq(col="power", limit=100.0):
    return ConstraintDef(col=col, ctype="leq", limit_type="constant", limit_value=limit)

def make_geq(col="thrust", limit=50.0):
    return ConstraintDef(col=col, ctype="geq", limit_type="constant", limit_value=limit)

def make_eq(col="Cm", target=0.0, tol=0.01):
    return ConstraintDef(col=col, ctype="eq", target=target, tolerance=tol,
                         limit_type="constant", limit_value=None)


# ─── Constant limit ──────────────────────────────────────────────────────────

def test_leq_constant_feasible():
    c = make_leq("power", 100.0)
    row = {"speed": 50.0, "power": 80.0}
    sat, margin = evaluate_deterministic(row, c)
    assert sat
    assert math.isclose(margin, 20.0, rel_tol=1e-6)

def test_leq_constant_infeasible():
    c = make_leq("power", 100.0)
    row = {"speed": 50.0, "power": 120.0}
    sat, _ = evaluate_deterministic(row, c)
    assert not sat

def test_geq_constant_feasible():
    c = make_geq("thrust", 50.0)
    row = {"speed": 50.0, "thrust": 60.0}
    sat, margin = evaluate_deterministic(row, c)
    assert sat
    assert math.isclose(margin, 10.0, rel_tol=1e-6)

def test_eq_within_tolerance():
    c = make_eq("Cm", target=0.0, tol=0.01)
    row = {"Cm": 0.005}
    sat, _ = evaluate_deterministic(row, c)
    assert sat

def test_eq_outside_tolerance():
    c = make_eq("Cm", target=0.0, tol=0.01)
    row = {"Cm": 0.05}
    sat, _ = evaluate_deterministic(row, c)
    assert not sat


# ─── Expression limit ────────────────────────────────────────────────────────

def test_expression_limit():
    c = ConstraintDef(col="power", ctype="leq",
                      limit_type="expression", limit_value="0.5 * speed**2")
    row = {"speed": 10.0, "power": 40.0}  # limit = 0.5*100 = 50 → satisfied
    sat, _ = evaluate_deterministic(row, c)
    assert sat

def test_expression_limit_numpy():
    c = ConstraintDef(col="Cm", ctype="leq",
                      limit_type="expression", limit_value="sin(pi/6)")  # = 0.5
    row = {"Cm": 0.3}
    sat, _ = evaluate_deterministic(row, c)
    assert sat


# ─── Expression injection security ──────────────────────────────────────────

INJECTION_ATTEMPTS = [
    "__import__('os').system('echo hacked')",
    "exec('import os')",
    "open('/etc/passwd').read()",
    "__builtins__['__import__']('os')",
    # Gate 4: AST whitelist now blocks lambda (ast.Lambda not in _ALLOWED_EXPR_NODES).
    "(lambda: None)()",
    # Gate 4: AST whitelist blocks attribute access (ast.Attribute not allowed).
    "().__class__.__mro__[-1].__subclasses__()",
    "globals()",
    "locals()",
]

@pytest.mark.parametrize("expr", INJECTION_ATTEMPTS)
def test_expression_injection_blocked(expr):
    c = ConstraintDef(col="power", ctype="leq",
                      limit_type="expression", limit_value=expr)
    row = {"speed": 50.0, "power": 100.0}
    with pytest.raises((NameError, TypeError, AttributeError, ValueError)):
        evaluate_deterministic(row, c)

def test_input_constraint_injection():
    for expr in INJECTION_ATTEMPTS:
        with pytest.raises((NameError, TypeError, AttributeError, ValueError)):
            evaluate_input_constraint(expr, {"speed": 50.0})

def test_input_constraint_class_traversal():
    """Gate 4: AST whitelist blocks attribute access (ast.Attribute), so the
    class-hierarchy traversal attack now raises ValueError before eval() runs."""
    expr = "().__class__.__mro__[-1].__subclasses__()"
    with pytest.raises(ValueError, match="disallowed construct"):
        evaluate_input_constraint(expr, {"speed": 50.0})


def test_table_constraint_path_traversal():
    """Gate 4: Path outside UPLOAD_DIR must raise ValueError — path traversal
    attempt via a crafted limit_value is rejected before any file is opened."""
    c = ConstraintDef(
        col="power", ctype="leq",
        limit_type="table",
        limit_value="../../../etc/passwd",
        table_condition_cols=["speed"],
        table_limit_col="limit",
    )
    row = {"speed": 50.0, "power": 100.0}
    with pytest.raises(ValueError, match="outside the upload directory"):
        evaluate_deterministic(row, c)


# ─── Feasibility probability ─────────────────────────────────────────────────

def test_p_feasible_leq_high_when_below_limit():
    c = make_leq("power", 100.0)
    prob = p_feasible(mu=50.0, sigma=5.0, constraint=c, row_vars={})
    assert prob > 0.99

def test_p_feasible_leq_low_when_above_limit():
    c = make_leq("power", 100.0)
    prob = p_feasible(mu=150.0, sigma=5.0, constraint=c, row_vars={})
    assert prob < 0.01

def test_p_feasible_in_unit_interval():
    c = make_leq("power", 100.0)
    prob = p_feasible(mu=100.0, sigma=10.0, constraint=c, row_vars={})
    assert 0.0 <= prob <= 1.0

def test_p_feasible_eq_centered():
    c = make_eq("Cm", target=0.0, tol=0.05)
    prob = p_feasible(mu=0.0, sigma=0.001, constraint=c, row_vars={})
    assert prob > 0.99

def test_all_p_feasible_product():
    constraints = [make_leq("power", 100.0), make_geq("thrust", 10.0)]
    mu_dict    = {"power": 50.0, "thrust": 60.0}
    sigma_dict = {"power": 1.0,  "thrust": 1.0}
    product, probs = all_p_feasible(mu_dict, sigma_dict, constraints, {})
    assert len(probs) == 2
    assert math.isclose(product, probs[0] * probs[1], rel_tol=1e-9)
    assert product > 0.95


# ─── Input constraint ────────────────────────────────────────────────────────

def test_input_constraint_satisfied():
    assert evaluate_input_constraint("chord * twist <= 15.0", {"chord": 2.0, "twist": 5.0})

def test_input_constraint_violated():
    assert not evaluate_input_constraint("chord * twist <= 15.0", {"chord": 4.0, "twist": 5.0})
