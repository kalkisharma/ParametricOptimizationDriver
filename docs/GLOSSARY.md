# Glossary
## Parametric Optimization Driver — v1.1.6

---

## A

**Acquisition function**
A function that decides where to sample next, given the current surrogate model. It balances *exploitation* (sampling where the objective is predicted to be good) against *exploration* (sampling where the surrogate is uncertain). The three acquisition strategies in this tool are MaxVariance, CEI, and FeasibilitySearch.

**Anisotropic kernel**
A kernel where each input dimension has its own length-scale hyperparameter. This allows the GP to learn that some inputs have more influence than others at different spatial scales. Contrast with *isotropic kernel*, where all dimensions share one length-scale.

---

## B

**Bayesian optimization**
An iterative strategy for optimizing expensive black-box functions. A cheap surrogate model (here: a GP) is fitted to existing data; an acquisition function selects the next point to evaluate; the new observation is added; and the surrogate is re-fitted. Requires far fewer function evaluations than grid search or random sampling.

**Bounds**
User-specified minimum and maximum values for each input variable. The acquisition search is restricted to within these bounds. Tight bounds help the optimizer converge faster; overly narrow bounds may exclude the true optimum.

---

## C

**CEI — Constrained Expected Improvement**
The acquisition function used in Optimization mode. It combines Expected Improvement on the objective with the probability of satisfying all constraints:

```
CEI(x) = EI(x) × ∏ p_feasible_n(x)
```

Points with high predicted improvement but low constraint probability are penalized. Falls back to *FeasibilitySearch* when no feasible point exists in the current data.

**Cold start**
The state when no simulation results are available yet. The tool generates a *Latin Hypercube Sampling* design as the first experiment, covering the input space uniformly without any surrogate model.

**Constraint**
A requirement that a simulated output satisfies a condition at the suggested input. Three types: `leq` (value ≤ limit), `geq` (value ≥ limit), `eq` (|value − target| ≤ tolerance). The limit can be a constant, a Python expression over input variables, or a table lookup.

**Convergence chart**
A chart on the Results page showing how the best objective value found has improved across iterations (uploads). A plateau indicates the optimizer is converging.

---

## E

**EI — Expected Improvement**
The unconstrained acquisition function component. At a candidate point x:

```
EI(x) = E[max(f(x) − f*, 0)]
```

where f* is the best objective value seen so far. Computed analytically from the GP posterior normal distribution.

**Exploration vs exploitation trade-off**
The fundamental tension in Bayesian optimization: should the next sample be taken where the model *predicts* a good outcome (exploitation), or where the model is *uncertain* (exploration)? The ξ (xi) parameter in EI controls this balance — higher ξ favors exploration.

**Extrapolation**
When a suggested input value falls outside the range of the training data for that variable. GP predictions are less reliable in extrapolation regions because the kernel has no data to condition on nearby. Highlighted in orange in the suggestions table.

---

## F

**FeasibilitySearch**
A fallback acquisition strategy activated when no feasible point exists in the current data. Instead of maximizing EI, it maximizes the joint constraint probability `∏ p_feasible_n(x)`, steering the next sample toward the feasible region.

---

## G

**GP — Gaussian Process**
A probabilistic model that places a distribution over functions. Given training data, it produces a posterior mean (the best prediction) and a posterior standard deviation (the uncertainty). The GP interpolates exactly at training points (σ = 0 there) and has increasing uncertainty away from data. Used here to model the relationship between input parameters and simulation outputs.

**GP hyperparameters**
The parameters of the kernel function that are fitted by maximizing the log marginal likelihood. For the Matérn 5/2 kernel: the length-scale(s) and the signal variance. Noise level is a separate hyperparameter representing measurement/simulation noise.

---

## I

**Input-space constraint**
A user-defined Python expression that restricts *which combinations of inputs* are physically valid. Example: `chord * twist <= 15.0`. Enforced during acquisition search to prevent the optimizer from suggesting impossible configurations.

**Isolation Forest**
An ensemble anomaly detection algorithm that isolates observations by randomly partitioning the feature space. Points that require fewer partitions to isolate are more anomalous. Used here (with `contamination=0.1`) alongside IQR to detect outliers in the uploaded dataset.

**IQR — Interquartile Range**
A robust measure of statistical spread: Q3 − Q1. A data point is flagged as a potential outlier if it falls more than 1.5 × IQR below Q1 or above Q3. Used in the outlier detection step alongside Isolation Forest.

---

## K

**Kernel / covariance function**
The function that defines how similar two input points are in GP terms. The kernel determines the smoothness and shape of the functions the GP can model. The default in this tool is the Matérn 5/2 kernel, which assumes continuous and once-differentiable functions.

**Kriging believer**
A batch selection strategy for surrogate refinement: after selecting the first candidate, the GP is updated by treating that candidate's predicted mean as if it were an observed value ("believing" the surrogate), then the next candidate is selected. Produces diverse batches without running multiple full optimizations.

---

## L

**Latin Hypercube Sampling (LHS)**
A space-filling design-of-experiments method. The input space is divided into equally probable intervals along each dimension and exactly one sample is taken from each interval. LHS covers the input space more uniformly than random sampling, making it a good initial experiment design.

**Length-scale**
A kernel hyperparameter that controls how quickly the correlation between two points decays with distance. A short length-scale means the function can vary rapidly; a long length-scale means it varies slowly. In anisotropic mode, each input has its own length-scale.

**LOO — Leave-One-Out cross-validation**
A diagnostic technique that estimates prediction accuracy: train on all-but-one data point, predict the withheld point, repeat for each point. LOO RMSE and R² here are computed analytically using the Cholesky decomposition of the GP kernel matrix — not by re-fitting the GP repeatedly. **Important:** LOO is a training-data metric; it does not guarantee accuracy on truly new designs.

**LOO R²**
Leave-one-out coefficient of determination. Values close to 1.0 indicate the GP fits the training data well. A low LOO R² may indicate insufficient data, a poor kernel choice, or a genuinely noisy/chaotic response.

**LOO RMSE**
Leave-one-out root-mean-square error. The average prediction error in the same units as the output column. Compare to the typical output range to judge practical significance.

---

## M

**Matérn 5/2 kernel**
A stationary kernel defined as:

```
k(r) = (1 + √5·r/ℓ + 5r²/(3ℓ²)) · exp(−√5·r/ℓ)
```

where r is the distance between two points and ℓ is the length-scale. Assumes the modelled function is twice continuously differentiable — a good default for smooth engineering simulations.

**MaxVariance**
The acquisition function used in Surrogate Refinement mode. Selects the point in the design space where GP predictive standard deviation (σ) is highest. With kriging-believer batch selection, it fills in the most uncertain regions of the design space.

---

## N

**Noise level**
A GP hyperparameter (also called the nugget) representing measurement or simulation noise. Fitted automatically. A large noise level indicates the GP interprets data scatter as noise rather than signal.

---

## O

**Objective function**
The quantity to be maximized or minimized. Can be any input or output column, or a weighted sum of columns. For the primary use case (maximize vehicle speed subject to trim constraints), the objective column is typically `speed`.

---

## P

**p_feasible**
The probability that a constraint is satisfied at a candidate point, computed from the GP posterior normal distribution for the relevant output. `p_feasible = 1` means the GP is certain the constraint is satisfied; `p_feasible = 0.5` means it is uncertain. Values below 0.5 are highlighted yellow in the suggestions table.

**Posterior**
The GP distribution over functions *after* conditioning on observed data. The posterior mean is the GP's best prediction; the posterior variance quantifies remaining uncertainty. Contrast with the *prior*, which is the GP distribution before observing any data.

---

## S

**Sobol sensitivity indices (S1)**
First-order Sobol indices quantify the fraction of output variance attributable to each input variable individually. S1 ∈ [0, 1]; higher means more influence. Computed via Monte Carlo sampling through the GP surrogate using the Saltelli estimator. The sum of all S1 can be ≤ 1 (interaction effects make up the remainder).

**Surrogate model**
A cheap-to-evaluate approximation of the expensive simulation. Here, a GP fitted to existing simulation data. The surrogate is re-fitted from scratch on each run using all available data.

**Surrogate Refinement mode**
Run mode that targets regions of highest GP uncertainty, building a globally accurate surrogate before any optimization is attempted. Useful early in a study when data is sparse.

---

## X

**ξ (xi) — exploration parameter**
A scalar that controls how much EI favors exploration. Computed automatically via:

```
ξ = 0.1 × exp(−n / max(5·d, 1))
```

where n is the number of training rows and d is the number of inputs. As data accumulates, ξ decays and the optimizer becomes more exploitative.

---

## Abbreviations Quick Reference

| Abbreviation | Full Term |
|---|---|
| CEI | Constrained Expected Improvement |
| EI | Expected Improvement |
| GP | Gaussian Process |
| GPR | Gaussian Process Regressor |
| IQR | Interquartile Range |
| LHS | Latin Hypercube Sampling |
| LOO | Leave-One-Out cross-validation |
| RMSE | Root-Mean-Square Error |
| S1 | First-order Sobol sensitivity index |
| SSE | Server-Sent Events |
