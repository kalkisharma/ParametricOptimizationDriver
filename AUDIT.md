# AUDIT LOG — Parametric Optimization Driver
# Repo: github.com/kalkisharma/ParametricOptimizationDriver
# Audit started: 2026-05-06
# Starting version: v1.1.0
# Team: PM, Security Engineer, Compliance Officer, Data Governance Lead,
#        Full-stack Developer, ML Engineer, Scientific Python Developer,
#        Domain Expert, QA Engineer, UI Designer, Interaction Designer,
#        Instructional Designer, Technical Writer

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** PM
**Supporting Roles:** None
**Finding:** Initialize VERSION (v1.1.0) and AUDIT.md for audit start.
**Severity:** LOW
**Change:** Created VERSION and AUDIT.md at repo root.
**Test Result:** NOT RUN
**Status:** RESOLVED

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** PM
**Supporting Roles:** None
**Finding:** README badge uses placeholder `YOUR_USERNAME/YOUR_REPO` — the CI badge URL on line 3 is not updated to the actual repo (`kalkisharma/ParametricOptimizationDriver`). All other README content accurately reflects the built tool: modes, constraint types, workflow, output CSV schema, architecture diagram, and project structure are consistent with the code. The `docs/` directory referenced in no current file but will be needed at Gate 7.
**Severity:** LOW
**Change:** None yet — deferred to Technical Writer at Gate 5/7.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Security Engineer
**Supporting Roles:** None
**Finding (Data Classification):** The tool carries no classification markings, no authentication, and no access controls. It is designed for localhost use only. Assumed data classification level: UNCLASSIFIED / CUI at most. If used with program simulation data that carries a higher classification, the deployment model (unauthenticated localhost Flask) is inappropriate. This finding is ON RECORD. No change made — classification level is a program-level decision outside the tool's scope.
**Severity:** MEDIUM
**Change:** None.
**Test Result:** NOT RUN
**Status:** OPEN — Program team must confirm acceptable classification level before operational use.

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Security Engineer
**Supporting Roles:** None
**Finding (Debug Mode):** `app.py` line 424 runs Flask with `debug=True` unconditionally: `app.run(debug=True, port=port, threaded=True)`. In debug mode, Werkzeug exposes an interactive Python console at the `/` error page, accessible to anyone on the network who can reach port 5000 and trigger an unhandled exception. This provides arbitrary code execution to any reachable party. This is a HIGH finding even for localhost-only deployments because the tool is intended to be shared across a team network.
**Severity:** HIGH
**Change:** To be fixed at Gate 2 — Full-stack Developer will gate debug mode behind an environment variable.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Security Engineer
**Supporting Roles:** None
**Finding (In-Memory Job Store):** `_jobs` dict retains `{queue, result, error, _surrogate}` for every run with no TTL and no eviction. Simulation data and GP model objects persist in process memory until the Flask process is restarted. For a single-user localhost session this is acceptable. For a multi-user or long-running deployment, this constitutes unbounded memory growth and a data residency risk: one user's data is accessible to any code path that can reach `_jobs`. Documented ON RECORD.
**Severity:** MEDIUM
**Change:** None at Gate 1 — Data Governance Lead will document lifecycle formally at Gate 2.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Security Engineer
**Supporting Roles:** None
**Finding (Dependencies Not Pinned):** `requirements.txt` uses `>=` version constraints for all packages (e.g., `flask>=3.0.0`, `numpy>=1.26.0`). This means a fresh install can pull any future major version, including breaking or security-relevant changes. No hash pinning (`pip-compile --generate-hashes`) is present. For a tool used in program workflows, exact version pinning with integrity verification is required.
**Severity:** MEDIUM
**Change:** To be addressed at Gate 2 — Security Engineer will produce a pinned requirements.txt.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Compliance Officer
**Supporting Roles:** None
**Finding (GP Model as Derivative of Controlled Data):** When the surrogate is trained on program simulation data, the fitted GP stores training inputs (X_train) and the kernel matrix inverse (alpha, L) in memory and in the job store. If the simulation data is ITAR-controlled technical data, the trained GP model parameters — which encode the physical relationships between inputs and outputs — likely constitute a derivative of controlled technical data. The `/export_report` endpoint embeds predicted values and sensitivity indices derived from that model into a downloadable HTML file. This finding is ON RECORD. The program team must make a formal determination before using this tool on controlled data. No change to the code is required unless the determination is that controls are needed.
**Severity:** MEDIUM
**Change:** None — compliance determination is a program decision.
**Test Result:** NOT RUN
**Status:** OPEN — Awaiting program compliance determination.

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Compliance Officer
**Supporting Roles:** None
**Finding (Download and Export Endpoint Authorization):** `/download/<job_id>` and `/export_report/<job_id>` require no authentication. Access control is provided only by the 128-bit UUID job_id, which is not guessable by brute force in practice. However, UUID job_ids are transmitted in browser URLs, network logs, and history — they are not secrets. For unclassified data in a single-user localhost session, this is acceptable. For team-network or classified deployments, authorization controls are required. ON RECORD.
**Severity:** MEDIUM
**Change:** None at Gate 1.
**Test Result:** NOT RUN
**Status:** OPEN — Program team must confirm acceptable for intended deployment.

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Compliance Officer
**Supporting Roles:** None
**Finding (Sobol Sensitivity Indices):** Sobol S1 indices reveal which input variables most strongly drive each output. For program data, this is a summary of the physical behavior of the system under test. If the underlying data is controlled, the sensitivity indices — which quantify input-output relationships — may constitute a summary of controlled technical data. The Sobol chart is embedded in the exported HTML report. ON RECORD.
**Severity:** MEDIUM
**Change:** None — compliance determination is a program decision.
**Test Result:** NOT RUN
**Status:** OPEN — Awaiting program compliance determination.

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Data Governance Lead
**Supporting Roles:** None
**Finding (_jobs Data Lifecycle):** `_jobs[job_id]` retains the following after a completed run:
  - `queue`: `queue.Queue` object (drained, but object persists)
  - `result`: full result dict including suggestions list, all Plotly chart JSON, diagnostics dict
  - `error`: full Python traceback string if run failed
  - `_surrogate`: `SurrogateModel` object containing `X_train` (normalized inputs), `Y_train` (standardized outputs), fitted `GaussianProcessRegressor` objects per output column, and `StandardScaler` objects

Retention duration: indefinite — until Flask process restart. No TTL, no LRU eviction, no explicit cleanup endpoint. For a typical CFD dataset (50 rows, 4 inputs, 3 outputs), the surrogate object is small (< 1 MB), so memory growth is not an immediate concern. However, across many sessions in a long-running deployment, this accumulates.
**Severity:** MEDIUM
**Change:** None at Gate 1 — documented for Gate 2 resolution.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Data Governance Lead
**Supporting Roles:** None
**Finding (Concurrent Access Behavior):** `_jobs` is a plain Python dict. Multiple simultaneous `/run` requests each spawn a daemon thread that writes to `_jobs[job_id]`. Python's GIL serializes individual dict operations (`__setitem__`, `__getitem__`) but does NOT protect compound read-modify-write sequences (e.g., checking existence then writing). The `/predict_row` route reads `_jobs[job_id]["_surrogate"]` from a different thread than the one that wrote it. This is safe in practice because the surrogate is written once (before `done` is emitted) and read-only thereafter — but this invariant is not enforced by any lock. Documented as a known-acceptable risk for single-user localhost use. A `threading.Lock` would make this invariant explicit.
**Severity:** LOW
**Change:** None at Gate 1.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Data Governance Lead
**Supporting Roles:** None
**Finding ("Stateless" Description Is Inaccurate):** PROGRESS.md line 18 states: "The tool is fully stateless — every session starts fresh from the uploaded data." This is incorrect. The tool is stateless in the sense that the GP model is re-fit from scratch on every `/run` call — it does not persist learned state across sessions. However, within a session, `_jobs[job_id]` retains the fitted surrogate, result dict, and CSV file in `UPLOAD_DIR` between `/run`, `/predict_row`, `/download`, and `/export_report` calls. The `/predict_row` endpoint explicitly depends on this retained state. The description should be corrected to: "The GP model is re-fit from scratch on every run. Intermediate results (fitted surrogate, suggestions) are retained in memory for the duration of the session and deleted when the process restarts."
**Severity:** LOW
**Change:** PROGRESS.md to be updated by Technical Writer at Gate 5/7.
**Test Result:** NOT RUN
**Status:** OPEN

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Domain Expert
**Supporting Roles:** None
**Finding (Workflow Model Adequacy):** The 5-step wizard (Upload → Preprocess → Configure → Run → Results) accurately mirrors the CFD design iteration loop. Each step maps to a real workflow phase: data collection → quality review → study configuration → analysis → next run selection. This is the correct workflow model for parametric CFD optimization studies. No change required.
**Severity:** LOW
**Change:** None.
**Test Result:** NOT RUN
**Status:** RESOLVED

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Domain Expert
**Supporting Roles:** None
**Finding (Acquisition Mode Sufficiency):** Three acquisition modes are implemented: MaxVariance (space-filling), CEI (constrained optimization), FeasibilitySearch (no-feasible-point fallback). For the stated primary use case (maximize speed subject to trim constraints), CEI + FeasibilitySearch is appropriate. For early-stage studies with no prior data, MaxVariance + cold-start LHS covers the space-filling need. The modes are sufficient for the target use cases as described. No change required.
**Severity:** LOW
**Change:** None.
**Test Result:** NOT RUN
**Status:** RESOLVED

---

## [v1.1.0] — 2026-05-06
**Gate:** 1 — Scope, Data, and Deployment Context
**Lead Role:** Domain Expert
**Supporting Roles:** None
**Finding (Single-Objective vs Multi-Objective):** The tool supports a single objective column or a weighted sum of columns. Real CFD program problems frequently require Pareto front optimization (e.g., maximize L/D while minimizing drag divergence Mach number). The current weighted-sum approach collapses this to a single scalar, which requires the user to know the correct weights in advance — a strong assumption. Multi-objective Bayesian optimization (e.g., Expected Hypervolume Improvement) is not implemented. This is a known limitation appropriate to document, not a defect. The tool is well-suited for the stated primary use case (single-objective speed maximization with constraints).
**Severity:** LOW
**Change:** Document as known limitation in README. Deferred to Gate 7 Technical Writer.
**Test Result:** NOT RUN
**Status:** OPEN — Deferred to Gate 7 documentation.

---

## Gate 1 PM Sign-Off — 2026-05-06
All four role findings (PM, Security Engineer, Compliance Officer, Data Governance Lead, Domain Expert) documented above. CRITICAL findings: none. HIGH findings: 1 (debug=True — tracked, to be resolved at Gate 2). MEDIUM and LOW findings carried forward with PM acknowledgment. Gate 1 is CLOSED. Gate 2 may begin.

---
