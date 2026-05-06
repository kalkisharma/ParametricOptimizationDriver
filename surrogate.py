# =============================================================================
# surrogate.py
# Parametric Optimization Driver
# Version: v1.1.2
# Role: ML Engineer
# Last modified: 2026-05-06
# Description: Gaussian Process surrogate model — fits one GP per output column
#              with automatic kernel selection (Matérn 5/2 vs RBF) via analytical
#              LOO RMSE, input normalization, output standardization, and uncertainty
#              calibrated prediction.
# =============================================================================

"""
Gaussian Process surrogate: fitting, prediction, uncertainty, and LOO diagnostics.
Kernel auto-selected (Matérn 5/2 vs RBF) via leave-one-out cross-validation RMSE.
"""

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import (
    RBF,
    ConstantKernel,
    Matern,
    WhiteKernel,
)
from sklearn.preprocessing import StandardScaler


def _make_kernel(kernel_type: str, n_features: int, anisotropic: bool):
    """Build a GP kernel. length_scale dimension matches n_features if anisotropic."""
    ls_init = np.ones(n_features) if anisotropic else 1.0
    ls_bounds = (1e-3, 1e3)

    if kernel_type == "rbf":
        base = RBF(length_scale=ls_init, length_scale_bounds=ls_bounds)
    else:
        base = Matern(length_scale=ls_init, length_scale_bounds=ls_bounds, nu=2.5)

    return ConstantKernel(1.0, (1e-3, 1e3)) * base + WhiteKernel(1e-4, (1e-10, 1e-1))


def _loo_rmse(gpr: GaussianProcessRegressor, X: np.ndarray, y: np.ndarray) -> float:
    """
    Analytical LOO RMSE for a fitted GPR (scikit-learn).
    Uses the identity: LOO error_i = alpha_i / K_inv_ii
    where alpha = K_inv @ y and K_inv is the inverse of the kernel matrix.
    Falls back to a loop if the analytical form is unavailable.
    """
    try:
        # sklearn stores L_ (Cholesky of K + noise*I) and alpha_
        L = gpr.L_
        alpha = gpr.alpha_.ravel()
        # K_inv diagonal via Cholesky solve
        L_inv = np.linalg.solve(L, np.eye(len(L)))
        K_inv_diag = np.sum(L_inv ** 2, axis=0)
        loo_errors = alpha / K_inv_diag
        return float(np.sqrt(np.mean(loo_errors ** 2)))
    except Exception:
        # fallback: manual LOO (slow but correct)
        n = len(y)
        errors = []
        for i in range(n):
            idx = [j for j in range(n) if j != i]
            gpr_tmp = GaussianProcessRegressor(
                kernel=gpr.kernel_,
                alpha=1e-10,
                normalize_y=False,
                n_restarts_optimizer=0,
            )
            gpr_tmp.fit(X[idx], y[idx])
            pred = gpr_tmp.predict(X[[i]])[0]
            errors.append(y[i] - pred)
        return float(np.sqrt(np.mean(np.array(errors) ** 2)))


def _loo_r2(gpr: GaussianProcessRegressor, X: np.ndarray, y: np.ndarray) -> float:
    """
    Analytical leave-one-out R² for a fitted GPR.

    NOTE: This is a TRAINING-DATA metric, not a held-out test metric.
    LOO scores are optimistic relative to true generalization performance,
    especially with small datasets. The UI should make this clear to users.
    """
    try:
        L = gpr.L_
        alpha = gpr.alpha_.ravel()
        L_inv = np.linalg.solve(L, np.eye(len(L)))
        K_inv_diag = np.sum(L_inv ** 2, axis=0)
        loo_preds = y - alpha / K_inv_diag
        ss_res = np.sum((y - loo_preds) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    except Exception:
        return 0.0


class SurrogateModel:
    """
    Fits one GP per output column. Supports:
    - Automatic kernel selection (Matérn 5/2 vs RBF) via LOO RMSE
    - Manual kernel override ('matern52', 'rbf', 'auto')
    - Anisotropic or isotropic length scales
    - Input normalization and output standardization
    """

    def __init__(
        self,
        kernel: str = "auto",
        n_restarts: int = 5,
        anisotropic: bool = True,
    ):
        self.kernel = kernel
        self.n_restarts = n_restarts
        self.anisotropic = anisotropic

        self._gps: dict[str, GaussianProcessRegressor] = {}
        self._x_scaler = StandardScaler()
        self._y_scalers: dict[str, StandardScaler] = {}
        self._kernel_used: dict[str, str] = {}
        self._diagnostics: dict[str, dict] = {}
        self.output_cols: list[str] = []
        self.input_cols: list[str] = []
        self._fitted = False

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        input_cols: list[str],
        output_cols: list[str],
    ) -> "SurrogateModel":
        """
        Fit one GP per output column.

        X: (n_samples, n_inputs)  — raw input values
        Y: (n_samples, n_outputs) — raw output values
        """
        self.input_cols = input_cols
        self.output_cols = output_cols
        n_features = X.shape[1]

        X_scaled = self._x_scaler.fit_transform(X)

        for i, col in enumerate(output_cols):
            y = Y[:, i]
            scaler = StandardScaler()
            y_scaled = scaler.fit_transform(y.reshape(-1, 1)).ravel()
            self._y_scalers[col] = scaler

            kernel_choice = self.kernel
            best_gp, best_rmse, best_kernel_name = None, np.inf, "matern52"

            candidates = (
                ["matern52", "rbf"] if kernel_choice == "auto" else [kernel_choice]
            )
            for kname in candidates:
                kern = _make_kernel(kname, n_features, self.anisotropic)
                gpr = GaussianProcessRegressor(
                    kernel=kern,
                    alpha=1e-10,
                    normalize_y=False,
                    n_restarts_optimizer=self.n_restarts,
                )
                gpr.fit(X_scaled, y_scaled)
                rmse = _loo_rmse(gpr, X_scaled, y_scaled)
                if rmse < best_rmse:
                    best_rmse = rmse
                    best_gp = gpr
                    best_kernel_name = kname

            self._gps[col] = best_gp
            self._kernel_used[col] = best_kernel_name

            # LOO diagnostics in original scale
            y_std = float(scaler.scale_[0]) if scaler.scale_ is not None else 1.0
            rmse_orig = best_rmse * y_std
            r2 = _loo_r2(best_gp, X_scaled, y_scaled)
            self._diagnostics[col] = {
                "r2": round(r2, 4),
                "rmse": round(rmse_orig, 6),
                "kernel": best_kernel_name,
            }

        self._fitted = True
        return self

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, X_new: np.ndarray) -> np.ndarray:
        """Return mean predictions (n_new, n_outputs) in original scale."""
        self._check_fitted()
        X_scaled = self._x_scaler.transform(X_new)
        preds = []
        for col in self.output_cols:
            mu_scaled = self._gps[col].predict(X_scaled)
            mu = self._y_scalers[col].inverse_transform(mu_scaled.reshape(-1, 1)).ravel()
            preds.append(mu)
        return np.column_stack(preds)

    def predict_with_std(self, X_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (mean, std) each (n_new, n_outputs) in original scale."""
        self._check_fitted()
        X_scaled = self._x_scaler.transform(X_new)
        means, stds = [], []
        for col in self.output_cols:
            mu_s, sigma_s = self._gps[col].predict(X_scaled, return_std=True)
            y_std = float(self._y_scalers[col].scale_[0])
            mu = self._y_scalers[col].inverse_transform(mu_s.reshape(-1, 1)).ravel()
            sigma = sigma_s * y_std
            means.append(mu)
            stds.append(sigma)
        return np.column_stack(means), np.column_stack(stds)

    def predict_single(self, x: np.ndarray) -> tuple[dict, dict]:
        """
        Predict for a single input vector.
        Returns ({col: mean}, {col: std}).
        """
        means, stds = self.predict_with_std(x.reshape(1, -1))
        mean_dict = {col: float(means[0, i]) for i, col in enumerate(self.output_cols)}
        std_dict = {col: float(stds[0, i]) for i, col in enumerate(self.output_cols)}
        return mean_dict, std_dict

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def loo_diagnostics(self) -> dict[str, dict]:
        """Return {col: {r2, rmse, kernel}} precomputed during fit."""
        self._check_fitted()
        return self._diagnostics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_fitted(self):
        """Raise RuntimeError if the model has not been fitted yet."""
        if not self._fitted:
            raise RuntimeError("SurrogateModel has not been fitted yet. Call fit() first.")

    def _scale_x(self, X: np.ndarray) -> np.ndarray:
        """Transform raw input array to StandardScaler-normalized space."""
        return self._x_scaler.transform(X)

    def get_gp(self, col: str) -> GaussianProcessRegressor:
        """Return the fitted GaussianProcessRegressor for a given output column."""
        self._check_fitted()
        return self._gps[col]

    def get_x_scaler(self):
        """Return the fitted StandardScaler for input normalization."""
        return self._x_scaler

    def get_y_scaler(self, col: str):
        """Return the fitted StandardScaler for the given output column."""
        return self._y_scalers[col]
