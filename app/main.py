from __future__ import annotations

import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services import ingest_jobs_for_companies, infer_roles, load_companies, rank_jobs, tailor_resume
from app.storage import DataStore

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Mage-Luz MVP")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
store = DataStore(BASE_DIR / "data" / "db.json")


def _extract_text(file_name: str, data: bytes) -> str:
    lower = file_name.lower()
    if lower.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")

    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            pdf = PdfReader(io.BytesIO(data))
            return "\n".join([p.extract_text() or "" for p in pdf.pages])
        except Exception:
            return ""

    if lower.endswith(".docx"):
        try:
            import docx

            doc = docx.Document(io.BytesIO(data))
            return "\n".join([p.text for p in doc.paragraphs])
        except Exception:
            return ""

    return data.decode("utf-8", errors="ignore")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    state = store.load()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "resumes": state.get("resumes", []),
            "companies": state.get("companies", []),
            "jobs": state.get("jobs", []),
            "matches": state.get("matches", []),
            "variants": state.get("variants", []),
        },
    )


@app.post("/seed-companies")
def seed_companies():
    state = store.load()
    state["companies"] = load_companies()
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    file_bytes = await file.read()
    resume_id = str(uuid.uuid4())
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "resume")
    out_path = UPLOAD_DIR / f"{resume_id}_{safe_name}"
    out_path.write_bytes(file_bytes)

    parsed_text = _extract_text(safe_name, file_bytes)
    role_profile = infer_roles(parsed_text)

    state = store.load()
    state.setdefault("resumes", []).append(
        {
            "id": resume_id,
            "file_name": safe_name,
            "file_path": str(out_path),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "parsed_text": parsed_text[:20000],
            "role_profile": role_profile,
        }
    )
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/ingest-jobs")
def ingest_jobs(company_ids: list[str] = Form(default=[])):
    state = store.load()
    companies = [c for c in state.get("companies", []) if c["id"] in company_ids]
    jobs = ingest_jobs_for_companies(companies)
    state["jobs"] = jobs
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/run-matching")
def run_matching(resume_id: str = Form(...)):
    state = store.load()
    resumes = state.get("resumes", [])
    jobs = state.get("jobs", [])
    resume = next((r for r in resumes if r["id"] == resume_id), None)
    if not resume:
        return RedirectResponse(url="/", status_code=303)

    matches = rank_jobs(resume, jobs, state.get("companies", []))
    state["matches"] = matches
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/tailor-top")
def tailor_top(resume_id: str = Form(...), top_n: int = Form(default=3)):
    state = store.load()
    resume = next((r for r in state.get("resumes", []) if r["id"] == resume_id), None)
    if not resume:
        return RedirectResponse(url="/", status_code=303)

    top_matches = sorted(state.get("matches", []), key=lambda x: x["final_score"], reverse=True)[:top_n]
    variants = []
    for m in top_matches:
        job = next((j for j in state.get("jobs", []) if j["id"] == m["job_id"]), None)
        if not job:
            continue
        variants.append(tailor_resume(resume, job, m))

    state["variants"] = variants
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/track-event")
def track_event(match_id: str = Form(...), event_type: str = Form(...)):
    state = store.load()
    state.setdefault("events", []).append(
        {
            "id": str(uuid.uuid4()),
            "match_id": match_id,
            "event_type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    for m in state.get("matches", []):
        if m["id"] == match_id:
            m["status"] = event_type
    store.save(state)
    return RedirectResponse(url="/", status_code=303)
