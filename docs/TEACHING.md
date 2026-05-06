# Teaching Guide
## Parametric Optimization Driver — v1.1.6

A pedagogical companion to the tool. Read this if you want to understand *why* it works, not just *how* to use it.

---

## 1. Why Not Just Run a Grid Search?

Suppose you have three input variables (speed, pitch, altitude) and you want to evaluate them at 10 levels each. That is 10³ = 1,000 simulation runs. At 30 minutes per run, that is three weeks of compute time.

Bayesian optimization reframes the problem: instead of evaluating everywhere, build a cheap mathematical model of the simulation (the *surrogate*), use it to decide which point to evaluate next, run only that point, update the model, and repeat. In practice, well-implemented Bayesian optimization typically finds near-optimal solutions in tens to a few hundred evaluations rather than thousands.

**The key insight:** evaluating one strategically chosen point gives more information than evaluating many points at random, because the surrogate propagates what you learned everywhere.

---

## 2. The Gaussian Process Surrogate

### What it is

A Gaussian Process (GP) is a probability distribution over functions. Instead of predicting a single number at a new point, it predicts a *distribution* — a mean and a standard deviation. The mean is the best guess; the standard deviation is the uncertainty.

Formally, a GP is specified by a mean function m(x) and a covariance function (kernel) k(x, x'). For this tool:

- **Prior mean:** zero (the GP starts with no prior belief about the function value)
- **Kernel:** Matérn 5/2 — a kernel that models functions which are smooth but not infinitely differentiable, appropriate for most engineering simulations

### What fitting does

Given n training points (X, y), fitting the GP solves for the kernel hyperparameters (length-scales, signal variance, noise level) by maximising the log marginal likelihood. This is done automatically using L-BFGS-B with multiple random restarts to avoid local optima.

After fitting, the GP posterior at a new point x* is:

```
mean(x*)  = k(x*, X) · [K + σ²I]⁻¹ · y
var(x*)   = k(x*, x*) - k(x*, X) · [K + σ²I]⁻¹ · k(X, x*)
```

where K is the n×n kernel matrix of the training data and σ² is the fitted noise variance.

### The interpolation property

At a training point, the GP posterior mean equals the observed value and the posterior variance is (approximately) zero. This is why the surrogate passes through the data — it "knows" the answer exactly where it has been told. Away from data, the variance grows and the GP reverts toward its prior mean.

This property is crucial for interpreting the suggestions table: orange cells (extrapolation) have higher uncertainty because the GP has no nearby data to anchor its prediction.

### Why Matérn 5/2?

The Matérn family has a smoothness parameter ν. At ν = 5/2, the kernel assumes the function is twice continuously differentiable. This is more realistic for CFD outputs than the squared-exponential kernel (which assumes infinitely smooth functions) and more appropriate than Matérn 3/2 (which assumes only once-differentiable). It is the standard choice for engineering optimization problems.

---

## 3. Leave-One-Out Diagnostics

### What LOO measures

LOO cross-validation measures how well the GP predicts each training point when that point is withheld from fitting. It answers the question: "If I had one fewer data point and used the GP to predict it, how close would I be?"

### How it is computed

A naive implementation would re-fit the GP n times (once for each withheld point). This is computationally prohibitive for large datasets. Instead, this tool uses the analytical Cholesky formula:

```
LOO residual_i = (α_i) / (K⁻¹)_ii
```

where α = [K + σ²I]⁻¹y (already computed during fitting) and (K⁻¹)_ii is the i-th diagonal element of the inverse kernel matrix (derived from the Cholesky factor L). This gives exact LOO predictions in O(n²) time without re-fitting.

### What LOO does NOT tell you

LOO R² and RMSE are **training-data diagnostics**. A GP with LOO R² = 0.98 fits the existing data well, but says nothing about how accurate it will be at a new, untested design that is far from any training point. In extrapolation regions (orange cells), actual accuracy may be much worse than LOO implies.

Use LOO to detect:
- Gross fitting failures (R² near 0 usually means too few data points or a degenerate kernel)
- Noisy outputs (high RMSE relative to output range)
- Output columns the GP struggles to model (different LOO R² per output)

Do not use LOO to certify accuracy of specific suggestions.

---

## 4. Acquisition Functions

### Mode 1: MaxVariance (Surrogate Refinement)

Selects the point x* where the GP is most uncertain:

```
x* = argmax σ(x)
```

This is pure exploration — it ignores the objective entirely and focuses on building a globally accurate surrogate. Use this mode when you have fewer than approximately 5–10 points per input dimension and the surrogate is not yet reliable enough to trust for optimization.

**Kriging believer batch selection:** to generate multiple suggestions, after selecting x₁ the GP is updated by setting y(x₁) = mean(x₁) (treating the prediction as a real observation), then x₂ is selected from this "updated" surrogate, and so on. This ensures the batch covers different uncertain regions rather than clustering at the single most uncertain point.

### Mode 2: CEI (Constrained Optimization)

The Constrained Expected Improvement acquisition:

```
CEI(x) = EI(x) × ∏ p_feasible_n(x)
```

**Expected Improvement:**

```
EI(x) = (μ(x) − f* − ξ) · Φ(z) + σ(x) · φ(z)
    z  = (μ(x) − f* − ξ) / σ(x)
```

where μ(x) and σ(x) are the GP posterior mean and standard deviation, f* is the current best feasible objective value, ξ is the exploration parameter, Φ is the standard normal CDF, and φ is the standard normal PDF.

When ξ is large, EI rewards exploration (high σ). When ξ is small, EI favors exploitation (high μ). ξ decays automatically as the dataset grows, so the optimizer starts exploratory and becomes more exploitative as data accumulates.

**Feasibility probability:**

For a constraint "output ≤ limit", using the GP posterior for that output (mean μ_c, std σ_c):

```
p_feasible = Φ((limit − μ_c) / σ_c)
```

For equality constraints (|output − target| ≤ tolerance):

```
p_feasible = Φ((target + tol − μ_c) / σ_c) − Φ((target − tol − μ_c) / σ_c)
```

CEI multiplies EI by all constraint probabilities, so a point with very high expected improvement but near-zero constraint probability still scores poorly. This is the mechanism that prevents the optimizer from suggesting constraint-violating designs.

### Fallback: FeasibilitySearch

If no training point satisfies all constraints (all `p_feasible` products are near zero), EI cannot be meaningfully computed because f* is undefined. FeasibilitySearch takes over:

```
x* = argmax ∏ p_feasible_n(x)
```

This steers the optimizer directly toward the feasible region. Once a feasible point is found and the next run confirms feasibility, the tool automatically returns to CEI.

---

## 5. Sobol Sensitivity Analysis

### What S1 means

The first-order Sobol index S1(i) is the fraction of total output variance that can be attributed to input i *acting alone* (without interactions with other inputs):

```
S1(i) = Var[E[Y | X_i]] / Var[Y]
```

A high S1 means that if you knew exactly the value of input i, you would eliminate a large fraction of uncertainty about the output. Inputs with S1 near zero barely influence the output.

**Practical use:** focus your sampling budget on inputs with high S1. Inputs with S1 ≈ 0 can potentially be fixed without significant loss of accuracy.

### How it is computed here

Direct Monte Carlo sampling through the GP surrogate using the Saltelli (2002) double-loop estimator. This avoids running the actual simulation and is computationally free once the GP is fitted.

**Caveat:** the S1 values reflect the GP surrogate, not the true simulation. If the surrogate is inaccurate (low LOO R²), the S1 values may be unreliable. With more data and better surrogate fits, S1 converges toward the true sensitivity of the simulation.

The estimator can occasionally produce slightly negative S1 values (clipped to 0) or a sum > 1 with finite samples — these are artifacts of the Monte Carlo estimator, not errors.

---

## 6. Constraint Expressions

### How they work

When you enter a constraint limit expression like `0.5 * rho * speed**2`, the tool:

1. Parses the expression into an Abstract Syntax Tree (AST)
2. Checks every node against a whitelist of allowed node types (arithmetic, comparisons, function calls to a safe subset of numpy/math functions)
3. Rejects any expression containing `ast.Attribute` (blocks `.__class__`, `.mro__`, etc.), `ast.Lambda`, `ast.Subscript`, comprehensions, or formatted strings
4. If the AST check passes, evaluates the expression with `__builtins__ = {}` and only the allowed names in scope

**Why two layers?** The AST check catches structural attacks (lambda, comprehensions). The builtins suppression prevents runtime name escapes. Neither is sufficient alone; together they provide defense-in-depth against code injection.

---

## 7. Interpreting the Results Page

### LOO diagnostics

| Value | Interpretation |
|---|---|
| R² ≥ 0.95 | Excellent surrogate fit for this output |
| R² 0.80–0.95 | Good fit; optimization should proceed |
| R² 0.50–0.80 | Mediocre — consider adding more training data |
| R² < 0.50 | Poor fit — may indicate insufficient data, wrong kernel, or genuinely chaotic output |

These thresholds are rough guides. Always consider the LOO RMSE in the context of the output range.

### Sensitivity chart

Sobol S1 bars give the ranking of input importance. Use this to:
- Identify which inputs to prioritize in future sampling
- Detect if an input you expected to be influential is near zero (check your data or bounds)
- Understand which outputs are input-insensitive (S1 near zero for all inputs = the GP has not learned any strong trend)

### Suggestions table

Each row is a candidate design point. Columns:
- Input columns: values to set in your CFD solver
- `pred_*`: GP predicted mean. Not a guaranteed value — treat as a central estimate
- `*_lower` / `*_upper`: ±2σ confidence interval. Narrow = confident; wide = uncertain
- `p_feasible_*`: 1 = almost certainly feasible; 0.5 = uncertain; <0.5 = predicted constraint violation (highlighted yellow)

**Orange cells** (extrapolation): the suggested value is outside the range of training data for that input. The GP's prediction may be unreliable. You can still run the simulation — you may just get a larger surprise.

**Editing the table:** you can manually adjust input values before downloading. The GP re-checks predictions live as you type, updating the `pred_*` and `p_feasible_*` columns. This lets you explore nearby designs interactively.

---

## 8. When to Stop

Bayesian optimization does not have a guaranteed stopping criterion, but practical rules of thumb:

1. **Convergence plateau:** if the best objective value has not improved for 3–5 iterations, the optimizer may have converged or reached a local optimum
2. **Tight confidence intervals:** if all `*_lower` and `*_upper` columns are close to `pred_*`, the surrogate is highly confident in its predictions
3. **High LOO R²:** once R² > 0.95 for all outputs, the surrogate is reliable enough to trust its optimum
4. **Budget exhaustion:** you have run as many simulations as your compute budget allows

At that point, the best feasible row in your full dataset (input columns at the highest objective value that satisfies all constraints) is your recommended operating point.

---

## 9. Common Pitfalls

**Pitfall: too few data points**
The GP needs at minimum about 3–5 points per input dimension to fit useful hyperparameters. With 2 inputs and 6 data points, optimization can work but is fragile. Run Surrogate Refinement first to get 10–20 baseline points before switching to Optimization mode.

**Pitfall: all data in one region**
If all training data covers only part of the input space, the GP will have high confidence in that region but high uncertainty elsewhere. The optimizer may suggest designs far from any existing data — that is intentional exploration, not a bug.

**Pitfall: constraint infeasibility from the start**
If none of your initial data satisfies the constraints, FeasibilitySearch activates. This is normal and expected early in a study. Add more diverse initial runs to cover the feasible region faster.

**Pitfall: LOO R² = 1.0**
Perfect LOO score can indicate overfitting, especially with very few data points and many hyperparameters. It does not mean the surrogate will generalize perfectly to new designs.

**Pitfall: output with no trend**
If Sobol S1 is near zero for all inputs and LOO R² is low, the output may be genuinely noisy or dominated by interactions rather than main effects. Consider whether that output is a useful constraint or objective target.
