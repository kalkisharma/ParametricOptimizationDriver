# Parametric Optimization Driver — Design Progress

**Date:** 2026-05-01  
**Status:** v1.1 — post-launch bug fixes and UX improvements in progress

---

## What This Tool Does

A Python/Flask web application that acts as a smart CFD optimization co-pilot. It does **not** run the CFD solver. Instead, it:

1. Accepts a CSV of prior simulation results (each row = one run; columns = inputs + outputs)
2. Fits a Gaussian Process surrogate to learn the input-output relationship
3. In **Surrogate Refinement mode**: suggests the next cases that fill the biggest gaps in the data space
4. In **Optimization mode**: suggests the next cases most likely to improve a user-defined objective while satisfying constraints
5. Outputs the suggested input conditions as an editable browser table and downloadable CSV

The user runs those CFD cases externally, adds the results to the CSV, and re-uploads. The GP model is **re-fit from scratch on every run**. Intermediate results (the fitted surrogate, suggestions, and uploaded CSV) are retained in server memory for the duration of the session and cleared when the process restarts.

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

**Safe expression evaluation:** Two-layer defense — (1) AST whitelist validates the expression parse tree before eval runs, blocking attribute access (`.__class__`), subscripting, lambda expressions, and comprehensions; (2) `__builtins__` is set to `{}` blocking all builtin name lookups. Only numpy math functions and current row variable names are in scope. Tested against injection suite (`__import__`, `exec`, `os`, `open`, `lambda`, class-traversal — all raise `ValueError` or `NameError`).

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
| 1 | Scaffold: requirements.txt, setup_github.py, README, git init | ✅ Complete |
| 2 | Flask skeleton: all routes, SSE endpoint, HTML stepper shell, dark/light CSS | ✅ Complete |
| 3 | Preprocessing: IQR + Isolation Forest, parallel-coords chart | ✅ Complete |
| 4 | Surrogate: GP fit, LOO kernel selection, diagnostics | ✅ Complete |
| 5 | Constraints: constant / expression / table-interp evaluators | ✅ Complete |
| 6 | Acquisition: MaxVariance, CEI, FeasibilitySearch, ξ auto-scaling | ✅ Complete |
| 7 | Sensitivity: Sobol indices via GP Monte Carlo | ✅ Complete |
| 8 | Report export: standalone HTML with embedded charts | ✅ Complete |
| 9 | Optimization orchestration: full pipeline, SSE progress, cold-start LHS | ✅ Complete |
| 10 | Frontend: stepper UX, config JSON, editable table, toasts, all charts | ✅ Complete |
| 11 | Tests: full pytest suite, error paths, golden outputs, CI workflow | ✅ Complete |
| 12 | Documentation: complete README with constraint syntax reference | ✅ Complete |
| 13 | UX: enhanced error reporting — inline field errors, expandable banner, structured pipeline error toast | ✅ Complete |
| 14 | Bug fixes: SSE JSON crash, run button double-click, scatter_matrix Plotly crash, numpy type serialization | ✅ Complete |
| 15 | UX: run button first-click fix, responsive charts, dark hover labels, contextual tooltip system | ✅ Complete |

---

## Post-v1 Improvements

### Error Reporting (2026-04-30)

**Problem:** Config validation banner showed only an error count with no detail. Pipeline failures showed a raw `ValueError` toast with no actionable context.

**Changes (commit `269b382`):**
- `validateConfig()` returns `{label, anchor/el}` error objects; banner expands to a scrollable bullet list via "▼ Show" toggle
- Live min/max bounds validation: red border + inline message fires on every keystroke; Run-click renders all inline field errors and scrolls to the first offending element
- New validation checks: objective column not selected (optimization mode), constraint missing limit/target value, integer bounds must be whole numbers
- Pipeline error toast shows plain-English exception message + "▼ Show details" (last 3 stack frames + full traceback) + "Download log" (saves `error_log.txt` with traceback and config snapshot)
- `app.py`: structured SSE error payload (`message`, `traceback`, `last_frames`, `config_snapshot`); basic payload validation in `/run` before thread spawn

### Bug Fixes & UX Improvements (2026-05-01)

**SSE pipeline crash — SurrogateModel not JSON serializable**
- Root cause: `result` dict (passed by reference into SSE queue) was mutated by re-adding `_surrogate` before `json.dumps` ran in the web thread — race condition.
- Fix: surrogate stored at `_jobs[job_id]["_surrogate"]` (separate key, never inside `result`). `predict_row` updated to read from `job["_surrogate"]`.

**Plotly scatter_matrix crash — `ValueError: Invalid value`**
- Root cause: `px.scatter_matrix()` called without `template=`, triggering a broken symbol property in the ambient Anaconda default template.
- Fix: added `template="plotly"` explicitly.

**SSE generator hardening**
- Added `_SafeEncoder` (JSONEncoder subclass) converting `np.integer`, `np.floating`, `np.bool_`, `np.ndarray` to plain Python types.
- Wrapped `json.dumps` in try/except inside `generate()` — serialization failures now emit a structured error SSE event instead of silently dropping the connection.

**Run button first-click silent fail**
- Root cause: `goToStep(4)` called before `unlockStep(4)`; guard `if (n > STATE.maxUnlockedStep)` caused silent return.
- Fix: `unlockStep(4)` added before `goToStep(4)`.

**Run button double-submit**
- Button disabled immediately on click; re-enabled on pipeline error, SSE disconnect, or cancel.

**Chart improvements**
- `{responsive: true}` added to all `Plotly.react` calls — charts resize with browser window.
- Scatter matrix: dynamic height (`max(500, n_cols × 120)`), larger markers (6 px), explicit font size.
- Scatter matrix promoted to full-width (`chart-card wide`) in results grid.
- Dark `hoverlabel` added to all charts (`bgcolor: #1e1e3a`, white text) — fixes white popup on dark theme.

**Contextual tooltip system**
- Lightweight floating tooltip engine in `main.js` — single `#tooltip-box` div, positioned on `mousemove`, triggered by `data-tip` attribute.
- CSS: `[data-tip]` elements get dotted underline + `cursor: help`.
- Tooltips (plain English + formula where relevant) on: column role select, integer checkbox, bounds labels, mode buttons, LOO R² / RMSE headers, Sobol S₁ title, Convergence title, Scatter Matrix title, Uncertainty Map title.

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
