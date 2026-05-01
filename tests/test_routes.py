"""Tests for Flask routes: HTTP status codes, SSE output, error paths, download."""

import io
import json
import time
import pytest

from tests.conftest import make_dataset


def _csv_bytes(df):
    return df.to_csv(index=False).encode()


# ─── /upload ─────────────────────────────────────────────────────────────────

def test_upload_valid_csv(client, aero_df):
    res = client.post("/upload",
                      data={"file": (io.BytesIO(_csv_bytes(aero_df)), "test.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 200
    data = res.get_json()
    assert "job_id" in data
    assert "columns" in data
    assert data["n_rows"] == len(aero_df)

def test_upload_no_file(client):
    res = client.post("/upload")
    assert res.status_code == 400
    assert "error" in res.get_json()

def test_upload_non_csv(client):
    res = client.post("/upload",
                      data={"file": (io.BytesIO(b"not a csv"), "test.txt")},
                      content_type="multipart/form-data")
    assert res.status_code == 400

def test_upload_empty_csv(client):
    res = client.post("/upload",
                      data={"file": (io.BytesIO(b"col1,col2\n"), "empty.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 400

def test_upload_no_numeric_columns(client):
    res = client.post("/upload",
                      data={"file": (io.BytesIO(b"name,label\nfoo,bar\nbaz,qux"), "text.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 400
    assert "no numeric" in res.get_json()["error"].lower()


# ─── /run ────────────────────────────────────────────────────────────────────

def test_run_missing_job_id(client):
    res = client.post("/run",
                      data=json.dumps({"mode": "refinement"}),
                      content_type="application/json")
    assert res.status_code == 400

def test_run_unknown_job_id(client):
    res = client.post("/run",
                      data=json.dumps({"job_id": "nonexistent-id", "mode": "refinement",
                                       "input_cols": ["x"], "output_cols": ["y"]}),
                      content_type="application/json")
    assert res.status_code == 404

def test_run_valid_job_returns_started(client, uploaded_job):
    payload = {
        "job_id": uploaded_job,
        "mode": "refinement",
        "input_cols": ["speed", "pitch"],
        "output_cols": ["thrust", "power", "Cm"],
        "bounds": {"speed": {"min": 20, "max": 100}, "pitch": {"min": -10, "max": 10}},
        "n_suggestions": 3,
        "gp_settings": {"kernel": "matern52", "n_restarts": 1, "length_scale_type": "anisotropic"},
        "dup_threshold": 0.01,
    }
    res = client.post("/run", data=json.dumps(payload), content_type="application/json")
    assert res.status_code == 200
    assert res.get_json()["status"] == "started"


# ─── /download ───────────────────────────────────────────────────────────────

def test_download_unknown_job(client):
    res = client.get("/download/nonexistent-job")
    assert res.status_code == 404

def test_download_no_result(client, uploaded_job):
    res = client.get(f"/download/{uploaded_job}")
    assert res.status_code == 404


# ─── /export_report ──────────────────────────────────────────────────────────

def test_export_report_unknown_job(client):
    res = client.get("/export_report/nonexistent-job")
    assert res.status_code == 404


# ─── /upload_constraint_table ────────────────────────────────────────────────

def test_upload_constraint_table_valid(client):
    csv = b"speed,power_limit\n20,5000\n50,4000\n100,3000\n"
    res = client.post("/upload_constraint_table",
                      data={"file": (io.BytesIO(csv), "table.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 200
    data = res.get_json()
    assert "table_id" in data
    assert "speed" in data["columns"]

def test_upload_constraint_table_no_file(client):
    res = client.post("/upload_constraint_table")
    assert res.status_code == 400


# ─── /predict_row ────────────────────────────────────────────────────────────

def test_predict_row_missing_params(client):
    res = client.post("/predict_row",
                      data=json.dumps({}),
                      content_type="application/json")
    assert res.status_code == 400

def test_predict_row_unknown_job(client):
    res = client.post("/predict_row",
                      data=json.dumps({"job_id": "unknown", "x_row": [1.0, 2.0]}),
                      content_type="application/json")
    assert res.status_code == 404
