# Parametric Optimization Driver

[![Tests](https://github.com/YOUR_USERNAME/YOUR_REPO/actions/workflows/test.yml/badge.svg)](https://github.com/YOUR_USERNAME/YOUR_REPO/actions/workflows/test.yml)

A Bayesian optimization co-pilot for CFD workflows. Upload your simulation results, define your objective and constraints, and get the next best input conditions to run — all in a browser-based UI.

---

## Purpose

Running parametric CFD studies manually is slow and wasteful. This tool acts as a smart surrogate-based optimizer that:

1. Learns the input-output relationship from your existing simulation data using a **Gaussian Process surrogate**
2. Suggests the **next most informative cases** to run (surrogate refinement) or the **next cases most likely to improve your objective** while satisfying constraints (optimization)
3. Produces a downloadable CSV of suggested input conditions ready to feed into your CFD solver

The tool does **not** run CFD itself. It is a data-driven decision engine that sits between your simulation runs.

---

## Modes

### Mode 1 — Surrogate Refinement
Fills gaps in the design space by targeting regions of highest prediction uncertainty. Use this when you want to build a reliable global surrogate before optimizing.

**Algorithm:** Maximum GP variance acquisition with kriging-believer batch selection.

### Mode 2 — Constrained Optimization
Finds input conditions that maximize (or minimize) a user-defined objective while satisfying all constraints.

**Algorithm:** Constrained Expected Improvement (CEI). Automatically falls back to feasibility search if no feasible point exists in the current data.

---

## Constraint System

Each constraint specifies:
- **Output column** — which simulation output to constrain
- **Type** — `eq` (equality: `|value − target| ≤ tolerance`), `leq` (`value ≤ limit`), or `geq` (`value ≥ limit`)
- **Limit source** — one of:
  - **Constant:** a fixed number, e.g. `100.0`
  - **Expression:** a Python expression over input variables and numpy functions, e.g. `0.5 * speed**2` or `sin(pitch) * 9.81`
  - **Table:** a CSV file with condition columns and a limit column, interpolated automatically

### Expression Syntax Reference

Expressions are evaluated with the current candidate row's input values in scope. Available functions:

| Symbol | Meaning |
|--------|---------|
| `sin(x)`, `cos(x)`, `tan(x)` | Trigonometric (radians) |
| `exp(x)`, `log(x)` | Exponential and natural log |
| `sqrt(x)` | Square root |
| `clip(x, a, b)` | Clamp x between a and b |
| `interp(x, xp, fp)` | 1-D linear interpolation |
| `pi`, `e` | Mathematical constants |

**Example expressions:**
```
0.5 * rho * speed**2          # dynamic pressure
9.81 * mass * sin(pitch)      # gravitational component
clip(0.9 * power_max, 0, 1e6) # 90% of max power
```

**Security:** No `import`, `exec`, `open`, or other builtins are available. Injections raise a `NameError`.

### Table Lookup Constraints

Upload a CSV where rows represent operating conditions and one column is the limit value. Example `power_limit_table.csv`:

```
speed,altitude,power_limit
10,0,5000
10,1000,4500
20,0,4800
20,1000,4200
```

The tool interpolates using `scipy.interpolate.LinearNDInterpolator`. Points outside the table range produce a warning toast.

### Input-Space Constraints (Optional)

Prevent physically invalid input combinations using Python expressions over input variables only. Example: `chord * twist <= 15.0`. These are enforced during the acquisition search so the optimizer never suggests an impossible combination.

---

## Objective Function

Select any column (input **or** output) to maximize or minimize. Use the weighted-sum mode to optimize a combination:

```
objective = w1 * col1 + w2 * col2 + ...
```

For the primary use case of maximizing vehicle speed subject to trim constraints, set the objective column to `speed` (an input) and add trim quantity constraints on the output columns.

---

## Workflow

```
1. Open http://localhost:5000 in Chrome or Edge
2. Drop your simulation CSV onto the upload zone
   └── Columns: any mix of input and output variables
   └── Rows: one completed simulation run per row
   └── NaN values in output columns = failed run (excluded automatically)

3. Review the preprocessing step
   └── Outliers flagged by IQR + Isolation Forest
   └── Toggle inclusion/exclusion per row
   └── Export outlier rows if needed

4. Configure
   └── Assign each column as Input or Output
   └── Set bounds for each input (pre-filled from data min/max)
   └── Mark any inputs as Integer if applicable
   └── Choose mode: Surrogate Refinement or Optimization
   └── (Optimization only) Set objective and add constraints
   └── Optional: Advanced GP Settings, input-space constraints

5. Click Run
   └── Progress bar streams live updates
   └── Results appear automatically when done

6. Review results
   └── Surrogate accuracy diagnostics (LOO R² per output)
   └── Sensitivity analysis (which inputs matter most)
   └── Suggested cases table (editable — tweak before downloading)
   └── Uncertainty and convergence charts

7. Download next_cases.csv
   └── Contains: suggested inputs + GP predictions + ±2σ + feasibility probabilities

8. Run your CFD solver on the suggested cases
9. Add the results as new rows in your CSV
10. Re-upload → repeat from step 2
```

---

## Cold Start (No Prior Data)

If you have no simulation results yet, the tool generates an initial space-filling design:

1. Define your variables (names, types, bounds) via the form or drop a header-only CSV
2. Set the number of initial cases
3. Download the Latin Hypercube Sampling design
4. Run those cases in your CFD solver
5. Fill in the output columns and re-upload to begin optimization

---

## Output CSV Format

`next_cases.csv` columns:

| Column | Description |
|--------|-------------|
| `<input_col>` | Suggested value for each input variable |
| `pred_<output_col>` | GP predicted mean for each output |
| `pred_<output_col>_lower` | Predicted mean − 2σ |
| `pred_<output_col>_upper` | Predicted mean + 2σ |
| `p_feasible_<constraint_n>` | Probability of satisfying constraint n |
| `timestamp` | UTC datetime of suggestion |

---

## Installation

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

Python 3.11+ recommended. Tested on Chrome and Edge (Chromium).

---

## GitHub Setup (One Time)

```bash
python setup_github.py --token YOUR_GITHUB_TOKEN --repo your-repo-name
# Add --private for a private repository
# Add --org your-org-name to create under an organization
```

---

## Project Structure

```
ParametricOptimizationDriver/
├── app.py               Flask routes and SSE streaming
├── surrogate.py         GP surrogate: fitting, prediction, uncertainty, LOO diagnostics
├── optimization.py      Pipeline orchestration: refinement, optimization, cold-start LHS
├── acquisition.py       Acquisition strategies: MaxVariance, CEI, FeasibilitySearch
├── constraints.py       Constraint evaluation: constant, expression, table interpolation
├── preprocessing.py     Outlier detection (IQR + Isolation Forest), NaN handling
├── sensitivity.py       First-order Sobol sensitivity indices via GP Monte Carlo
├── static/css/          Responsive CSS Grid, dark/light theme
├── static/js/           Stepper logic, Plotly charts, editable table, SSE listener
├── templates/           Single-page HTML
├── tests/               pytest suite (unit + integration + error paths + CI)
├── .github/workflows/   GitHub Actions CI
├── requirements.txt
├── PROGRESS.md          Design decisions and build status
└── setup_github.py      One-time GitHub repo creation script
```

---

## Status

See [PROGRESS.md](PROGRESS.md) for detailed design decisions and build phase status.
