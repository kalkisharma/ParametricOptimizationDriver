"""
Flask application: routes, SSE streaming, file upload handling, report export.
"""

import io
import json
import os
import queue
import tempfile
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cfd_opt_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory store for active jobs: job_id -> {queue, result, error}
_jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# CSV upload — returns column names, preview rows, and basic stats
# ---------------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.endswith(".csv"):
        return jsonify({"error": "Only CSV files are accepted"}), 400

    try:
        df = pd.read_csv(f)
    except Exception as exc:
        return jsonify({"error": f"Could not parse CSV: {exc}"}), 400

    if df.empty:
        return jsonify({"error": "CSV file is empty"}), 400

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols:
        return jsonify({"error": "CSV has no numeric columns — cannot build a surrogate"}), 400

    # Save to temp dir so /run can reload it without re-upload
    job_id = str(uuid.uuid4())
    csv_path = UPLOAD_DIR / f"{job_id}.csv"
    df.to_csv(csv_path, index=False)

    # Basic per-column stats for the UI
    stats = {}
    for col in numeric_cols:
        s = df[col].dropna()
        stats[col] = {
            "min": float(s.min()) if len(s) else None,
            "max": float(s.max()) if len(s) else None,
            "mean": float(s.mean()) if len(s) else None,
            "nan_count": int(df[col].isna().sum()),
        }

    return jsonify({
        "job_id": job_id,
        "columns": df.columns.tolist(),
        "numeric_columns": numeric_cols,
        "n_rows": len(df),
        "preview": df.head(10).to_dict(orient="records"),
        "stats": stats,
    })


# ---------------------------------------------------------------------------
# Constraint table upload — returns column names for the lookup table
# ---------------------------------------------------------------------------

@app.route("/upload_constraint_table", methods=["POST"])
def upload_constraint_table():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    try:
        df = pd.read_csv(f)
    except Exception as exc:
        return jsonify({"error": f"Could not parse constraint table: {exc}"}), 400

    table_id = str(uuid.uuid4())
    table_path = UPLOAD_DIR / f"table_{table_id}.csv"
    df.to_csv(table_path, index=False)

    return jsonify({
        "table_id": table_id,
        "columns": df.columns.tolist(),
        "n_rows": len(df),
    })


# ---------------------------------------------------------------------------
# Run — spawn background thread, return job_id immediately
# ---------------------------------------------------------------------------

@app.route("/run", methods=["POST"])
def run():
    payload = request.get_json(force=True)
    job_id = payload.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    csv_path = UPLOAD_DIR / f"{job_id}.csv"
    if not csv_path.exists():
        return jsonify({"error": "Data file not found — please re-upload the CSV"}), 404

    # Each job gets its own message queue
    msg_queue: queue.Queue = queue.Queue()
    _jobs[job_id] = {"queue": msg_queue, "result": None, "error": None}

    def worker():
        try:
            df = pd.read_csv(csv_path)
            _run_pipeline(df, payload, msg_queue, job_id)
        except Exception:
            tb = traceback.format_exc()
            _jobs[job_id]["error"] = tb
            msg_queue.put({"type": "error", "message": tb})
        finally:
            msg_queue.put({"type": "done"})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "started"})


def _run_pipeline(df, config, msg_queue, job_id):
    """Orchestrate the full pipeline and push SSE messages."""
    # Import here to avoid circular imports at module load time
    from optimization import run_refinement, run_optimization

    def emit(msg_type, message, **kwargs):
        msg_queue.put({"type": msg_type, "message": message, **kwargs})

    mode = config.get("mode", "refinement")
    emit("progress", f"Starting {mode} pipeline…", step=1, total=6)

    if mode == "refinement":
        result = run_refinement(df, config, emit)
    else:
        result = run_optimization(df, config, emit)

    # Cache surrogate object separately (not JSON-serialisable — kept in memory only)
    surrogate_obj = result.pop("_surrogate", None)
    _jobs[job_id]["result"] = result
    if surrogate_obj is not None:
        _jobs[job_id]["result"]["_surrogate"] = surrogate_obj
    emit("result", "Pipeline complete", data=result)


# ---------------------------------------------------------------------------
# SSE stream — browser connects here to receive live progress
# ---------------------------------------------------------------------------

@app.route("/stream/<job_id>")
def stream(job_id):
    if job_id not in _jobs:
        return jsonify({"error": "Unknown job_id"}), 404

    def generate():
        msg_queue = _jobs[job_id]["queue"]
        while True:
            try:
                msg = msg_queue.get(timeout=120)
            except queue.Empty:
                yield "event: heartbeat\ndata: {}\n\n"
                continue

            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("type") in ("done", "error"):
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Live row re-prediction (for editable suggestions table)
# ---------------------------------------------------------------------------

@app.route("/predict_row", methods=["POST"])
def predict_row():
    payload = request.get_json(force=True)
    job_id = payload.get("job_id")
    x_row = payload.get("x_row")

    if not job_id or x_row is None:
        return jsonify({"error": "job_id and x_row required"}), 400

    job = _jobs.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "No fitted model for this job"}), 404

    result = job["result"]
    surrogate = result.get("_surrogate")
    if surrogate is None:
        return jsonify({"error": "Surrogate not cached for this job"}), 404

    import numpy as np
    x = np.array(x_row, dtype=float).reshape(1, -1)
    means, stds = surrogate.predict_with_std(x)
    predictions = {}
    for j, col in enumerate(surrogate.output_cols):
        mu = float(means[0, j])
        sigma = float(stds[0, j])
        predictions[f"pred_{col}"] = round(mu, 6)
        predictions[f"pred_{col}_lower"] = round(mu - 2 * sigma, 6)
        predictions[f"pred_{col}_upper"] = round(mu + 2 * sigma, 6)

    return jsonify({"predictions": predictions})


# ---------------------------------------------------------------------------
# Download next_cases.csv
# ---------------------------------------------------------------------------

@app.route("/download/<job_id>")
def download(job_id):
    job = _jobs.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "No result available for this job"}), 404

    result = job["result"]
    suggestions_records = result.get("suggestions")
    if not suggestions_records:
        return jsonify({"error": "No suggestions in result"}), 404

    df = pd.DataFrame(suggestions_records)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)

    filename = f"next_cases_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        io.BytesIO(buf.read().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# ---------------------------------------------------------------------------
# Export standalone HTML report
# ---------------------------------------------------------------------------

@app.route("/export_report/<job_id>")
def export_report(job_id):
    job = _jobs.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "No result available for this job"}), 404

    result = job["result"]
    html = _build_report_html(result, job_id)
    buf = io.BytesIO(html.encode("utf-8"))
    filename = f"opt_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.html"
    return send_file(buf, mimetype="text/html", as_attachment=True, download_name=filename)


def _build_report_html(result, job_id):
    """Build a self-contained HTML report with embedded Plotly charts."""
    plots_json = result.get("plots", {})
    suggestions = result.get("suggestions", [])
    diagnostics = result.get("diagnostics", {})
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build suggestions table HTML
    if suggestions:
        df = pd.DataFrame(suggestions)
        table_html = df.to_html(index=False, classes="report-table", border=0)
    else:
        table_html = "<p>No suggestions generated.</p>"

    # Build diagnostics rows
    diag_rows = ""
    for col, d in diagnostics.items():
        r2 = d.get("r2", 0)
        rmse = d.get("rmse", 0)
        if r2 >= 0.95:
            badge = '<span class="badge good">✓ Good</span>'
        elif r2 >= 0.80:
            badge = '<span class="badge fair">⚠ Fair</span>'
        else:
            badge = '<span class="badge poor">✗ Poor</span>'
        diag_rows += f"<tr><td>{col}</td><td>{r2:.4f}</td><td>{rmse:.4f}</td><td>{badge}</td></tr>"

    # Build Plotly chart divs
    chart_divs = ""
    for name, fig_json in plots_json.items():
        div_id = f"chart_{name}"
        chart_divs += f"""
        <div class="chart-block">
          <h3>{name.replace('_', ' ').title()}</h3>
          <div id="{div_id}"></div>
          <script>Plotly.react('{div_id}', {json.dumps(fig_json.get('data', []))},
            {json.dumps(fig_json.get('layout', {}))});</script>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Optimization Report — {timestamp}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 24px; }}
  h1 {{ color: #7eb8f7; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  h2 {{ color: #a8c8ff; margin-top: 32px; }}
  h3 {{ color: #c0d8ff; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
  table.report-table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
  table.report-table th {{ background: #2a2a4a; color: #a8c8ff; padding: 8px 12px; text-align: left; }}
  table.report-table td {{ padding: 6px 12px; border-bottom: 1px solid #2a2a4a; }}
  table.report-table tr:hover td {{ background: #22223a; }}
  .diag-table {{ border-collapse: collapse; width: auto; margin: 16px 0; font-size: 13px; }}
  .diag-table th {{ background: #2a2a4a; color: #a8c8ff; padding: 8px 16px; text-align: left; }}
  .diag-table td {{ padding: 6px 16px; border-bottom: 1px solid #2a2a4a; }}
  .badge {{ padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }}
  .badge.good {{ background: #1a4a1a; color: #6fcf6f; }}
  .badge.fair {{ background: #4a3a00; color: #f0c040; }}
  .badge.poor {{ background: #4a1a1a; color: #f07070; }}
  .chart-block {{ margin: 24px 0; padding: 16px; background: #1e1e3a; border-radius: 8px; }}
</style>
</head>
<body>
<h1>Optimization Report</h1>
<p class="meta">Generated: {timestamp} &nbsp;|&nbsp; Job: {job_id}</p>

<h2>Surrogate Diagnostics</h2>
<table class="diag-table">
  <thead><tr><th>Output</th><th>LOO R²</th><th>LOO RMSE</th><th>Quality</th></tr></thead>
  <tbody>{diag_rows}</tbody>
</table>

<h2>Charts</h2>
{chart_divs}

<h2>Suggested Next Cases</h2>
{table_html}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port, threaded=True)
