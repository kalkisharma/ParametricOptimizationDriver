# CSV Schema Reference
## Parametric Optimization Driver — v1.1.6

---

## Upload CSV (Input)

### Format Requirements

| Requirement | Detail |
|---|---|
| File type | `.csv` (comma-separated values) |
| Header row | Required — first row must be column names |
| Encoding | UTF-8 |
| Rows | One completed simulation run per row |
| Columns | Any mix of input and output variables |
| Numeric | All columns used in the model must be numeric |
| NaN / missing | Allowed in output columns — rows with NaN outputs are excluded from GP fitting |
| Max file size | 50 MB |

### Column Roles

After upload, each numeric column is assigned a role in the Configure step:

| Role | Meaning |
|---|---|
| **Input** | A variable the optimizer can suggest values for. The user sets bounds (min/max). |
| **Output** | A variable the GP learns to predict. Can be a constraint target. |
| **Ignore** | Excluded from the model entirely (e.g., run ID, timestamp columns). |

Non-numeric columns are always ignored.

### NaN Handling

- Rows where **any used column** (input or output) has a NaN value are silently excluded from GP fitting.
- The count of excluded rows is shown in a banner on the Results page.
- NaN rows are not included in exported `next_cases.csv`.
- NaN in an output column = a failed simulation run (solver diverged, boundary violation, etc.).

### Cold Start (Header-Only CSV)

If you have no simulation results yet, upload a CSV with a header row and no data rows:

```
speed,pitch,thrust,power,Cm
```

The tool enters cold-start mode and generates a Latin Hypercube Sampling design without fitting a surrogate.

### Example Upload CSV

```
speed,pitch,thrust,power,Cm
45.2,3.1,12.4,91.3,0.003
67.8,-2.5,8.1,308.5,-0.021
82.0,5.0,18.7,552.0,0.015
50.0,0.0,,200.0,0.001
```

Row 4 has a missing thrust value — it will be excluded from the surrogate fit.

---

## Output CSV (`next_cases.csv`)

Downloaded via the **⬇ Download next\_cases.csv** button on the Results page.

### Column Definitions

| Column pattern | Type | Description |
|---|---|---|
| `<input_col>` | float | Suggested value for each input variable within specified bounds |
| `pred_<output_col>` | float | GP predicted mean for each output column |
| `pred_<output_col>_lower` | float | Predicted mean minus 2σ (95% confidence lower bound) |
| `pred_<output_col>_upper` | float | Predicted mean plus 2σ (95% confidence upper bound) |
| `p_feasible_<n>` | float [0–1] | Probability of satisfying constraint n (1 = certainly satisfied) |
| `timestamp` | ISO 8601 UTC | Date and time the suggestion was generated |

### Notes on Prediction Intervals

- `pred_<output_col>_lower` and `_upper` represent ±2σ from the GP posterior.
- At training data points, σ ≈ 0 so lower ≈ upper ≈ pred (GP interpolates at training points).
- For suggestions that **extrapolate** beyond the training data range, σ can be large — treat predictions with caution.

### Notes on Feasibility Probabilities

- `p_feasible_<n>` = P(output satisfies constraint n), computed from the GP posterior normal distribution.
- Values < 0.5 are highlighted yellow in the UI (predicted constraint violation).
- The joint feasibility probability (product of all `p_feasible_<n>`) drives the CEI acquisition function.
- For equality constraints (`|output - target| ≤ tolerance`), `p_feasible` = P(output within ±tolerance of target).

### Example Output CSV

```
speed,pitch,pred_thrust,pred_thrust_lower,pred_thrust_upper,pred_power,...,p_feasible_0,timestamp
92.4,4.2,21.3,18.1,24.5,786.4,...,0.82,2026-05-06T14:23:11+00:00
85.0,6.1,19.8,16.0,23.6,614.1,...,0.71,2026-05-06T14:23:11+00:00
```

---

## Constraint Table CSV (Table Lookup Limits)

Uploaded via the **Table CSV** option in the constraint builder.

### Format

| Column | Role |
|---|---|
| One or more **condition columns** | Input variables used as interpolation keys |
| One **limit column** | The constraint limit value at that operating condition |

### Example

```
speed,altitude,power_limit
10,0,5000
10,1000,4500
20,0,4800
20,1000,4200
```

- Condition columns: `speed`, `altitude`
- Limit column: `power_limit`
- Interpolation: `scipy.interpolate.LinearNDInterpolator` (linear, convex hull)
- Points outside the convex hull of the table return NaN → treated as 50% feasible in CEI

### Security Note

Constraint table CSV files must be uploaded via the `/upload_constraint_table` endpoint. Paths outside the upload directory are rejected. A constraint `limit_value` path that tries to reference files outside the upload directory (e.g., `../../etc/passwd`) raises a `ValueError` before any file is opened.
