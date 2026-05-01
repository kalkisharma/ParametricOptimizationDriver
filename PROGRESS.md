# Parametric Optimization Driver — Design Progress

**Date:** 2026-04-30  
**Status:** Design complete — implementation starting

---

## What This Tool Does

A Python/Flask web application that acts as a smart CFD optimization co-pilot. It does **not** run the CFD solver. Instead, it:

1. Accepts a CSV of prior simulation results (each row = one run; columns = inputs + outputs)
2. Fits a Gaussian Process surrogate to learn the input-output relationship
3. In **Surrogate Refinement mode**: suggests the next cases that fill the biggest gaps in the data space
4. In **Optimization mode**: suggests the next cases most likely to improve a user-defined objective while satisfying constraints
5. Outputs the suggested input conditions as an editable browser table and downloadable CSV

The user runs those CFD cases externally, adds the results to the CSV, and re-uploads. The tool is fully **stateless** — every session starts fresh from the uploaded data.

**Primary use case:** Maximize vehicle speed (an input variable) subject to trim constraints (e.g., force/moment balance within tolerance), where the surrogate models all constraint outputs and the optimizer searches for the highest feasible speed.

---

## Architecture Decisions

### Surrogate Model
- **Algorithm:** Gaussian Process Regression (GPR) via scikit-learn
- **Kernel:** Automatic selection between Matérn 5/2 and RBF via leave-one-out cross-validation RMSE. Manual override in Advanced GP Settings.
- **Per-output GPs:** One independent GP per output column
- **Normalization:** Inputs normalized to [0,1]; outputs standardized (z-score) before fitting
- **Hyperparameters:** Auto-optimized via marginal likelihood maximization; advanced settings expose number of restarts (default 5) and isotropic vs. anisotropic length scales
- **Minimum data:** n_inputs + 1 hard minimum; yellow warning banner below 2×n_inputs

### Optimization
- **Mode 1 — Surrogate Refinement:** Maximize prediction variance (space-filling); batch via kriging believer
- **Mode 2 — Constrained Bayesian Optimization:** Constrained Expected Improvement (CEI); auto-switch to feasibility search if no feasible point exists in current data
- **Objective:** Any column (input OR output) or weighted sum; supports "maximize speed" where speed is an input variable
- **No feasible region:** Auto-fallback to maximize probability of constraint satisfaction; banner notice shown
- **Convergence signal:** Max-CEI threshold banner (user-defined); chart of best feasible objective vs. row index
- **Exploration ξ:** Auto-scaled from dataset size — ξ=0.1×exp(−n_rows/(5×n_inputs)); no session memory needed

### Constraint System
Each constraint defines:
- **Output column** to constrain (GP-modeled)
- **Type:** equality (`|value − target| ≤ tol`), inequality (`≤` or `≥`)
- **Limit source:** constant value, Python expression, or uploaded lookup table (CSV, interpolated via `scipy.interpolate.LinearNDInterpolator`)

Optional **input-space constraints** (Python expressions on input variables only) block physically invalid input combinations.

**Safe expression evaluation:** Strict allowlist — only numpy math functions (`sin`, `cos`, `exp`, `log`, `sqrt`, `interp`, `clip`, `pi`, `e`) plus current row variable names. `__builtins__` is `{}`. Tested against injection suite (`__import__`, `exec`, `os`, `open`, `lambda`, etc. — all must raise NameError).

### Batch Selection
- **Size:** User-configurable, default 5
- **Strategy:** Kriging believer — after each selection, set that point's output to GP mean, refit, select next
- **Duplicate filter:** Reject suggestions within user-defined normalized Euclidean distance threshold (default 1% of input space diagonal)
- **Discrete inputs:** Mixed integer/continuous; integer dimensions rounded post-optimization

### Data Preprocessing (before GP fitting)
- **Outlier detection:** IQR (per-column, 1.5×IQR) + Isolation Forest (multivariate, contamination=0.1) — union of both flagged sets
- **NaN/failed rows:** Silently dropped; warning count shown
- **UI:** Interactive parallel-coordinates Plotly chart with outlier color overlay; user toggles row inclusion; outlier rows exportable as CSV

---

## UI/UX Design

### Layout & Theme
- **Layout:** Responsive CSS Grid — 3-panel side-by-side on ≥1440p, single-column on ≤1080p
- **Theme:** Dark mode default; light mode toggle (top-right icon); CSS custom properties for all colors
- **Browser target:** Chrome and Edge (Chromium), latest 2 versions

### Navigation
- **Numbered stepper wizard:** ①Upload → ②Preprocess → ③Configure → ④Run → ⑤Results
- Steps unlock sequentially; back navigation always allowed
- **Empty state:** Drop zone + "How it works" paragraph + "Try with sample data" button (built-in synthetic aerodynamics CSV: speed, pitch → thrust, power, Cm)
- **Unsaved config guard:** Confirmation dialog on new CSV drop when config exists ("Save config & continue" / "Continue without saving")

### Configuration Persistence
- **Save Config:** Downloads a JSON file (column roles, bounds, constraints, objective, GP settings)
- **Load Config:** Re-applies saved JSON to any new CSV upload; matches by column name

### Validation
- Inline red error messages appear on-blur next to each field
- Run button stays disabled until all errors are resolved
- Error count summary shown at top of config panel

### Visualizations (Results Dashboard)
| Chart | Details |
|---|---|
| LOO diagnostics table | R² + RMSE per output; color + icon + text label: ✓ Good (≥0.95) / ⚠ Fair (0.80–0.95) / ✗ Poor (<0.80) |
| Sobol sensitivity | First-order S1 bar chart per output (GP-based Monte Carlo) |
| Scatter matrix | Input-output pairwise plots colored by GP prediction; click point → full-row sidebar |
| Uncertainty heatmap | 2D GP std slice; user-selectable axes; click point → full-row sidebar |
| Convergence chart | Best feasible objective vs. row index; CEI-threshold banner if triggered |
| Suggested cases table | Editable cells; live GP re-check on change; constraint violations highlighted yellow |

### Notifications & Errors
- **Toasts:** Slide in from top-right; auto-dismiss 4s (info/success); persist until dismissed (errors); notification history icon in header
- **Error reporting:** Descriptive toast (plain-English cause + fix hint) + "Download error log" link (full Python traceback)

### Results Export
- **"Download next_cases.csv":** Input columns + predicted mean + ±2σ + feasibility probability per constraint + timestamp
- **"Export Report (HTML)":** Standalone self-contained HTML file with all Plotly charts embedded + suggested-cases table; dark CSS inlined for offline viewing

### Cold Start (No Prior Data)
- Variable definition form OR header-only CSV drop
- Generates Latin Hypercube Sampling initial design
- User sets variable names, types (continuous/integer), and bounds

---

## Testing Strategy

### Scope
- **pytest** unit and integration tests for all backend modules
- Flask test client for route and SSE endpoint testing
- No browser automation — frontend tested manually

### Test Data
Generated programmatically in `conftest.py` using analytic functions with known ground truth:
- `thrust(speed, pitch) = sin(pitch) * speed²`
- `power(speed, pitch) = speed³ * 0.01`
- `Cm(speed, pitch) = cos(pitch) − 0.1 * speed`

### Coverage by Module
| Module | What is tested |
|---|---|
| `surrogate.py` | Fit accuracy on analytic function; LOO R² > 0.95 on sufficient data; kernel selection; anisotropic length scales |
| `constraints.py` | All three limit types; feasibility probabilities ∈ [0,1]; injection security suite (all must raise NameError) |
| `acquisition.py` | MaxVariance targets high-variance region; CEI finds known 1D optimum; FeasibilitySearch reaches feasible region; no-feasible-point auto-switch; fixed-seed golden outputs within tolerance |
| `preprocessing.py` | Injected anomalies flagged; NaN rows excluded; count accuracy |
| `sensitivity.py` | S1 sums ≤ 1; dominant input identified for known function |
| `optimization.py` | Full pipeline output shape; cold-start LHS within bounds; convergence flag fires at threshold |
| `app.py` | All HTTP routes return correct status; SSE stream emits expected events; /download returns valid CSV headers; error path JSON; injection attempt on /run |

### Error Path Tests (one per condition)
- GP matrix singular (duplicate rows / too few points)
- No feasible point → auto-switch verified
- Invalid constraint expression → NameError returned as JSON
- Table interpolation out of range → extrapolation warning
- Missing required config fields → validation error JSON
- CSV with no numeric columns → upload error

### Performance Tests (timing assertions)
- GP fit, 50 rows × 4 inputs: < 10 seconds
- Acquisition optimization, batch of 5: < 30 seconds
- Outlier detection, 100 rows: < 2 seconds

### CI/CD
- **GitHub Actions** (`.github/workflows/test.yml`): `pip install -r requirements.txt && pytest` on Python 3.11, triggered on every push
- Pass/fail badge shown in README

---

## Project File Structure

```
ParametricOptimizationDriver/
├── app.py                   # Flask routes, SSE streaming, report export
├── surrogate.py             # GP fitting, LOO CV kernel selection, diagnostics
├── optimization.py          # Orchestration: refinement, constrained-EI, cold-start LHS
├── acquisition.py           # Abstract AcquisitionStrategy + MaxVariance, CEI, FeasibilitySearch
├── constraints.py           # ConstraintDef dataclass; constant/expression/table evaluators
├── preprocessing.py         # Outlier detection (IQR + Isolation Forest), NaN handling
├── sensitivity.py           # Sobol index computation via GP Monte Carlo
├── static/
│   ├── css/style.css        # Responsive CSS Grid, dark/light custom properties, stepper styles
│   └── js/main.js           # Stepper logic, SSE listener, drag-and-drop, validation,
│                            # Plotly charts, editable table with live re-check, toasts
├── templates/
│   └── index.html           # Single-page app shell
├── tests/
│   ├── conftest.py          # Analytic fixture generators, Flask test client, fixed seeds
│   ├── test_surrogate.py
│   ├── test_constraints.py
│   ├── test_acquisition.py
│   ├── test_preprocessing.py
│   ├── test_sensitivity.py
│   ├── test_optimization.py
│   └── test_routes.py
├── .github/
│   └── workflows/
│       └── test.yml         # GitHub Actions CI
├── requirements.txt
├── README.md
├── PROGRESS.md              # This file
└── setup_github.py          # One-time GitHub repo creation + push script
```

---

## Build Phases

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffold: requirements.txt, setup_github.py, README, git init | **Starting** |
| 2 | Flask skeleton: all routes, SSE endpoint, HTML stepper shell, dark/light CSS | Not started |
| 3 | Preprocessing: IQR + Isolation Forest, parallel-coords chart | Not started |
| 4 | Surrogate: GP fit, LOO kernel selection, diagnostics | Not started |
| 5 | Constraints: constant / expression / table-interp evaluators | Not started |
| 6 | Acquisition: MaxVariance, CEI, FeasibilitySearch, ξ auto-scaling | Not started |
| 7 | Sensitivity: Sobol indices via GP Monte Carlo | Not started |
| 8 | Report export: standalone HTML with embedded charts | Not started |
| 9 | Optimization orchestration: full pipeline, SSE progress, cold-start LHS | Not started |
| 10 | Frontend: stepper UX, config JSON, editable table, toasts, all charts | Not started |
| 11 | Tests: full pytest suite, error paths, golden outputs, CI workflow | Not started |
| 12 | Documentation: complete README with constraint syntax reference | Not started |

---

## Dependencies

```
flask
numpy
pandas
scikit-learn
scipy
plotly
gitpython
PyGithub
pytest
```

---

## How to Run

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000 in browser
```

**Initial GitHub setup (one time):**
```bash
python setup_github.py --token YOUR_GITHUB_TOKEN --repo your-repo-name
```
