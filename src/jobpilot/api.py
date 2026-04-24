"""FastAPI backend for the React frontend. Serves persisted pipeline data."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "pipeline_jobs.json"

app = FastAPI(title="JobPilot API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/jobs")
def list_jobs():
    if not DATA_FILE.exists():
        raise HTTPException(status_code=404, detail="No pipeline data yet. Run a search first.")
    return json.loads(DATA_FILE.read_text())


@app.get("/health")
def health():
    return {"ok": True}
