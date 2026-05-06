# =============================================================================
# acquisition.py
# Parametric Optimization Driver
# Version: v1.1.2
# Role: ML Engineer
# Last modified: 2026-05-06
# Description: Acquisition strategies for Bayesian optimization — MaxVariance
#              (space-filling), ConstrainedEI (constrained optimization), and
#              FeasibilitySearch (no-feasible-point fallback). All use
#              scipy differential_evolution with kriging-believer batch selection.
# =============================================================================

"""
Acquisition strategies for Bayesian optimization.

Abstract base: AcquisitionStrategy
Implementations:
  MaxVarianceAcquisition    — surrogate refinement (space-filling)
  ConstrainedEIAcquisition  — constrained expected improvement (optimization)
  FeasibilitySearchAcquisition — maximize P(feasible) when no feasible point exists
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod

import numpy as np
from scipy.optimize import differential_evolution
from scipy.stats import norm

from constraints import ConstraintDef, all_p_feasible
from surrogate import SurrogateModel


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AcquisitionStrategy(ABC):
    @abstractmethod
    def suggest(
        self,
        surrogate: SurrogateModel,
        bounds: list[tuple[float, float]],
        n: int,
        existing_X: np.ndarray,
        constraints: list[ConstraintDef] | None = None,
        input_constraints: list[str] | None = None,
        integer_dims: list[int] | None = None,
        dup_threshold: float = 0.01,
        **kwargs,
    ) -> np.ndarray:
        """
        Suggest n new input points.
        Returns ndarray of shape (n, n_inputs).
        """


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_bounds(bounds: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return bounds as a list of (float lo, float hi) tuples."""
    return [(float(lo), float(hi)) for lo, hi in bounds]


def _diagonal(bounds: list[tuple[float, float]]) -> float:
    """Euclidean length of the input-space diagonal (used to normalize distances)."""
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    return float(np.linalg.norm(hi - lo))


def _normalize_x(x: np.ndarray, bounds: list[tuple[float, float]]) -> np.ndarray:
    """Map x from input space to the unit hypercube [0, 1]^d."""
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    rng = hi - lo
    rng[rng == 0] = 1.0
    return (x - lo) / rng


def _is_duplicate(x: np.ndarray, existing: np.ndarray, bounds, threshold: float) -> bool:
    """Return True if x is within `threshold` (normalized Euclidean) of any point in existing."""
    if len(existing) == 0:
        return False
    diag = _diagonal(bounds)
    if diag == 0:
        return False
    x_norm = _normalize_x(x, bounds)
    existing_norm = np.array([_normalize_x(e, bounds) for e in existing])
    dists = np.linalg.norm(existing_norm - x_norm, axis=1)
    return bool(np.min(dists) < threshold)


def _round_integers(x: np.ndarray, integer_dims: list[int] | None) -> np.ndarray:
    """Round specified dimensions of x to the nearest integer (in-place copy)."""
    if integer_dims:
        x = x.copy()
        for d in integer_dims:
            x[d] = round(x[d])
    return x


def _check_input_constraints(x: np.ndarray, input_cols: list[str], exprs: list[str]) -> bool:
    """Return True if x satisfies all input-space constraint expressions. Fails safe (False) on errors."""
    if not exprs:
        return True
    from constraints import evaluate_input_constraint
    row_vars = dict(zip(input_cols, x.tolist()))
    try:
        return all(evaluate_input_constraint(e, row_vars) for e in exprs)
    except Exception:
        return False


def _xi_from_dataset_size(n_rows: int, n_inputs: int) -> float:
    """Exploration parameter: high when data is sparse, decays as data grows."""
    return 0.1 * np.exp(-n_rows / max(5 * n_inputs, 1))


# ---------------------------------------------------------------------------
# Kriging believer: refit surrogate with a pending point set to GP mean
# ---------------------------------------------------------------------------

def _kriging_believer_refit(
    surrogate: SurrogateModel,
    pending_X: np.ndarray,
    original_X: np.ndarray,
    original_Y: np.ndarray,
) -> SurrogateModel:
    """
    Refit surrogate after appending pending points with their GP-mean predictions.
    Returns a new SurrogateModel (does not mutate the original).
    """
    pend_Y = surrogate.predict(pending_X)
    new_X = np.vstack([original_X, pending_X])
    new_Y = np.vstack([original_Y, pend_Y])

    new_surrogate = SurrogateModel(
        kernel=surrogate.kernel,
        n_restarts=1,  # fast refit for believer
        anisotropic=surrogate.anisotropic,
    )
    new_surrogate.fit(new_X, new_Y, surrogate.input_cols, surrogate.output_cols)
    return new_surrogate


# ---------------------------------------------------------------------------
# MaxVarianceAcquisition
# ---------------------------------------------------------------------------

class MaxVarianceAcquisition(AcquisitionStrategy):
    """
    Selects points that maximize total GP prediction variance (sum across outputs).
    Batch via kriging believer.
    """

    def suggest(
        self,
        surrogate: SurrogateModel,
        bounds: list[tuple[float, float]],
        n: int,
        existing_X: np.ndarray,
        constraints=None,
        input_constraints=None,
        integer_dims=None,
        dup_threshold: float = 0.01,
        **kwargs,
    ) -> np.ndarray:
        bounds = _normalize_bounds(bounds)
        current_surrogate = surrogate
        pending_X = []
        original_X = kwargs.get("X_train", existing_X)
        original_Y = kwargs.get("Y_train")
        input_cols = surrogate.input_cols

        for _ in range(n):
            def neg_variance(x):
                _, stds = current_surrogate.predict_with_std(x.reshape(1, -1))
                return -float(np.sum(stds ** 2))

            result = differential_evolution(
                neg_variance,
                bounds,
                seed=42,
                maxiter=300,
                tol=1e-6,
                popsize=12,
                mutation=(0.5, 1.0),
                recombination=0.9,
            )
            x_best = _round_integers(result.x, integer_dims)

            # Replace duplicate with a random point within bounds
            if _is_duplicate(x_best, existing_X, bounds, dup_threshold):
                for _ in range(20):
                    x_rand = np.array([
                        np.random.uniform(b[0], b[1]) for b in bounds
                    ])
                    x_rand = _round_integers(x_rand, integer_dims)
                    if not _is_duplicate(x_rand, existing_X, bounds, dup_threshold):
                        x_best = x_rand
                        break

            pending_X.append(x_best)

            if original_Y is not None and len(pending_X) < n:
                pend = np.array([x_best])
                current_surrogate = _kriging_believer_refit(
                    current_surrogate, pend, original_X, original_Y
                )

        return np.array(pending_X)


# ---------------------------------------------------------------------------
# ConstrainedEIAcquisition
# ---------------------------------------------------------------------------

class ConstrainedEIAcquisition(AcquisitionStrategy):
    """
    Constrained Expected Improvement:
      CEI(x) = EI(x) × Π P_feasible_i(x)

    If the objective column is an input variable, EI uses the input value directly
    (no GP uncertainty on it — just compare to best feasible input value).

    Auto-delegates to FeasibilitySearchAcquisition when no feasible point exists.
    """

    def suggest(
        self,
        surrogate: SurrogateModel,
        bounds: list[tuple[float, float]],
        n: int,
        existing_X: np.ndarray,
        constraints=None,
        input_constraints=None,
        integer_dims=None,
        dup_threshold: float = 0.01,
        **kwargs,
    ) -> np.ndarray:
        constraints = constraints or []
        input_constraints = input_constraints or []
        bounds = _normalize_bounds(bounds)

        objective_spec = kwargs.get("objective_spec", {})
        X_train: np.ndarray = kwargs.get("X_train", existing_X)
        Y_train: np.ndarray = kwargs.get("Y_train")
        input_cols: list[str] = surrogate.input_cols
        output_cols: list[str] = surrogate.output_cols

        # Identify best feasible point in training data
        best_f, feasible_mask = self._best_feasible(
            X_train, Y_train, constraints, input_cols, output_cols, objective_spec
        )

        if not feasible_mask.any():
            # No feasible point — delegate to feasibility search
            fs = FeasibilitySearchAcquisition()
            result = fs.suggest(
                surrogate, bounds, n, existing_X,
                constraints=constraints,
                input_constraints=input_constraints,
                integer_dims=integer_dims,
                dup_threshold=dup_threshold,
                **kwargs,
            )
            return result

        xi = _xi_from_dataset_size(len(X_train), len(input_cols))
        current_surrogate = surrogate
        pending_X = []

        for _ in range(n):
            def neg_cei(x_flat):
                x = x_flat.reshape(1, -1)
                row_vars = dict(zip(input_cols, x_flat.tolist()))

                # Input constraint check
                if input_constraints and not _check_input_constraints(
                    x_flat, input_cols, input_constraints
                ):
                    return 0.0

                mu_dict, sigma_dict = {}, {}
                if Y_train is not None:
                    means, stds = current_surrogate.predict_with_std(x)
                    for j, col in enumerate(output_cols):
                        mu_dict[col] = float(means[0, j])
                        sigma_dict[col] = float(stds[0, j])

                # EI on objective
                ei = self._expected_improvement(
                    x_flat, row_vars, mu_dict, sigma_dict,
                    best_f, xi, objective_spec, input_cols, output_cols
                )

                # Feasibility product
                p_feas, _ = all_p_feasible(mu_dict, sigma_dict, constraints, row_vars)

                return -float(ei * p_feas)

            result = differential_evolution(
                neg_cei,
                bounds,
                seed=42,
                maxiter=400,
                tol=1e-7,
                popsize=15,
            )
            x_best = _round_integers(result.x, integer_dims)

            if _is_duplicate(x_best, existing_X, bounds, dup_threshold):
                for _ in range(20):
                    x_rand = np.array([np.random.uniform(b[0], b[1]) for b in bounds])
                    x_rand = _round_integers(x_rand, integer_dims)
                    if not _is_duplicate(x_rand, existing_X, bounds, dup_threshold):
                        x_best = x_rand
                        break

            pending_X.append(x_best)

            if Y_train is not None and len(pending_X) < n:
                pend = np.array([x_best])
                current_surrogate = _kriging_believer_refit(
                    current_surrogate, pend, X_train, Y_train
                )

        return np.array(pending_X)

    def _best_feasible(
        self, X, Y, constraints, input_cols, output_cols, objective_spec
    ) -> tuple[float, np.ndarray]:
        """
        Identify the best feasible objective value in the training data.

        Returns (best_f, feasible_mask) where best_f is the maximum signed objective
        over all feasible training rows and feasible_mask is a boolean array.
        Returns (-inf, all-False) when no feasible point exists.
        """
        if Y is None or len(Y) == 0:
            return -np.inf, np.zeros(len(X), dtype=bool)

        feasible = np.ones(len(X), dtype=bool)
        for c in constraints:
            if c.col not in output_cols:
                continue
            col_idx = output_cols.index(c.col)
            y_col = Y[:, col_idx]
            for i, (x_row, y_val) in enumerate(zip(X, y_col)):
                row_vars = dict(zip(input_cols, x_row.tolist()))
                row_vars[c.col] = float(y_val)
                from constraints import evaluate_deterministic
                satisfied, _ = evaluate_deterministic(row_vars, c)
                if not satisfied:
                    feasible[i] = False

        if not feasible.any():
            return -np.inf, feasible

        obj_col = objective_spec.get("column")
        direction = objective_spec.get("direction", "maximize")
        sign = 1.0 if direction == "maximize" else -1.0

        if obj_col in input_cols:
            col_idx = input_cols.index(obj_col)
            obj_vals = X[:, col_idx]
        elif obj_col in output_cols:
            col_idx = output_cols.index(obj_col)
            obj_vals = Y[:, col_idx]
        else:
            # Weighted sum
            weights = objective_spec.get("weights", {})
            obj_vals = np.zeros(len(X))
            for wcol, w in weights.items():
                if wcol in input_cols:
                    obj_vals += w * X[:, input_cols.index(wcol)]
                elif wcol in output_cols:
                    obj_vals += w * Y[:, output_cols.index(wcol)]

        best_f = float(np.max(obj_vals[feasible] * sign))
        return best_f, feasible

    def _expected_improvement(
        self, x_flat, row_vars, mu_dict, sigma_dict,
        best_f, xi, objective_spec, input_cols, output_cols
    ) -> float:
        """
        Compute Expected Improvement at x, handling three objective cases:
        - Input column: exact value (no GP uncertainty), EI reduces to step function
        - Output column: standard EI using GP (mu, sigma)
        - Weighted sum: combined EI using linear combination of GP predictions

        Sign convention: both best_f and the objective mu are multiplied by `sign`
        (1.0 for maximize, -1.0 for minimize) so EI always measures improvement
        toward a higher signed value.
        """
        obj_col = objective_spec.get("column")
        direction = objective_spec.get("direction", "maximize")
        sign = 1.0 if direction == "maximize" else -1.0

        if obj_col in input_cols:
            # Exact value — no GP uncertainty
            col_idx = input_cols.index(obj_col)
            f_new = sign * x_flat[col_idx]
            improvement = f_new - best_f - xi
            return float(max(0.0, improvement))

        elif obj_col in output_cols:
            mu = sign * mu_dict.get(obj_col, 0.0)
            sigma = sigma_dict.get(obj_col, 1e-9)
            if sigma <= 0:
                sigma = 1e-9
            z = (mu - best_f - xi) / sigma
            ei = (mu - best_f - xi) * norm.cdf(z) + sigma * norm.pdf(z)
            return float(max(0.0, ei))

        else:
            # Weighted sum
            weights = objective_spec.get("weights", {})
            mu = 0.0
            sigma_sq = 0.0
            for wcol, w in weights.items():
                if wcol in input_cols:
                    mu += sign * w * x_flat[input_cols.index(wcol)]
                elif wcol in output_cols:
                    mu += sign * w * mu_dict.get(wcol, 0.0)
                    sigma_sq += (sign * w * sigma_dict.get(wcol, 1e-9)) ** 2
            sigma = float(np.sqrt(sigma_sq)) or 1e-9
            z = (mu - best_f - xi) / sigma
            ei = (mu - best_f - xi) * norm.cdf(z) + sigma * norm.pdf(z)
            return float(max(0.0, ei))

    def max_cei_value(
        self,
        surrogate: SurrogateModel,
        bounds: list[tuple[float, float]],
        X_train: np.ndarray,
        Y_train: np.ndarray,
        constraints: list[ConstraintDef],
        objective_spec: dict,
    ) -> float:
        """Return the maximum CEI value over the input space (for convergence detection)."""
        best_f, feasible = self._best_feasible(
            X_train, Y_train, constraints,
            surrogate.input_cols, surrogate.output_cols, objective_spec
        )
        if not feasible.any():
            return 0.0

        xi = _xi_from_dataset_size(len(X_train), len(surrogate.input_cols))
        bounds = _normalize_bounds(bounds)

        def neg_cei(x_flat):
            x = x_flat.reshape(1, -1)
            row_vars = dict(zip(surrogate.input_cols, x_flat.tolist()))
            means, stds = surrogate.predict_with_std(x)
            mu_dict = {col: float(means[0, j]) for j, col in enumerate(surrogate.output_cols)}
            sigma_dict = {col: float(stds[0, j]) for j, col in enumerate(surrogate.output_cols)}
            ei = self._expected_improvement(
                x_flat, row_vars, mu_dict, sigma_dict,
                best_f, xi, objective_spec,
                surrogate.input_cols, surrogate.output_cols
            )
            p_feas, _ = all_p_feasible(mu_dict, sigma_dict, constraints, row_vars)
            return -float(ei * p_feas)

        result = differential_evolution(neg_cei, bounds, seed=42, maxiter=200, popsize=10)
        return float(-result.fun)


# ---------------------------------------------------------------------------
# FeasibilitySearchAcquisition
# ---------------------------------------------------------------------------

class FeasibilitySearchAcquisition(AcquisitionStrategy):
    """
    Maximizes the product of constraint feasibility probabilities.
    Used when no feasible point exists in the training data.
    """

    def suggest(
        self,
        surrogate: SurrogateModel,
        bounds: list[tuple[float, float]],
        n: int,
        existing_X: np.ndarray,
        constraints=None,
        input_constraints=None,
        integer_dims=None,
        dup_threshold: float = 0.01,
        **kwargs,
    ) -> np.ndarray:
        constraints = constraints or []
        bounds = _normalize_bounds(bounds)
        input_cols = surrogate.input_cols
        output_cols = surrogate.output_cols
        X_train = kwargs.get("X_train", existing_X)
        Y_train = kwargs.get("Y_train")
        current_surrogate = surrogate
        pending_X = []

        for _ in range(n):
            def neg_p_feas(x_flat):
                x = x_flat.reshape(1, -1)
                row_vars = dict(zip(input_cols, x_flat.tolist()))
                means, stds = current_surrogate.predict_with_std(x)
                mu_dict = {col: float(means[0, j]) for j, col in enumerate(output_cols)}
                sigma_dict = {col: float(stds[0, j]) for j, col in enumerate(output_cols)}
                p, _ = all_p_feasible(mu_dict, sigma_dict, constraints, row_vars)
                return -p

            result = differential_evolution(neg_p_feas, bounds, seed=42, maxiter=300, popsize=12)
            x_best = _round_integers(result.x, integer_dims)

            if _is_duplicate(x_best, existing_X, bounds, dup_threshold):
                for _ in range(20):
                    x_rand = np.array([np.random.uniform(b[0], b[1]) for b in bounds])
                    x_rand = _round_integers(x_rand, integer_dims)
                    if not _is_duplicate(x_rand, existing_X, bounds, dup_threshold):
                        x_best = x_rand
                        break

            pending_X.append(x_best)

            if Y_train is not None and len(pending_X) < n:
                pend = np.array([x_best])
                current_surrogate = _kriging_believer_refit(
                    current_surrogate, pend, X_train, Y_train
                )

        return np.array(pending_X)
