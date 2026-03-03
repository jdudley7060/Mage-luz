from __future__ import annotations

import io
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.services import ingest_jobs_for_companies, infer_roles, load_companies, rank_jobs, tailor_resume
from app.storage import DataStore

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Mage-Luz MVP")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"))
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


def _current_user(request: Request, state: dict):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return next((u for u in state.get("users", []) if u["id"] == user_id), None)


def _require_user(request: Request, state: dict):
    user = _current_user(request, state)
    if not user:
        return None, RedirectResponse(url="/login", status_code=303)
    return user, None


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), preferred_locations: str = Form(default="New York,San Francisco")):
    state = store.load()
    if any(u["email"].lower() == email.lower() for u in state.get("users", [])):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Email already exists."})

    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password": password,
        "is_paid": False,
        "plan": "free",
        "preferred_locations": [x.strip() for x in preferred_locations.split(",") if x.strip()],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state.setdefault("users", []).append(user)
    store.save(state)
    request.session["user_id"] = user["id"]
    return RedirectResponse(url="/", status_code=303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    state = store.load()
    user = next((u for u in state.get("users", []) if u["email"].lower() == email.lower() and u["password"] == password), None)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials."})
    request.session["user_id"] = user["id"]
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.post("/mock-upgrade")
def mock_upgrade(request: Request):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect
    user["is_paid"] = True
    user["plan"] = "pro_7_99"
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    resumes = [r for r in state.get("resumes", []) if r.get("user_id") == user["id"]]
    matches = [m for m in state.get("matches", []) if m.get("user_id") == user["id"]]
    variants = [v for v in state.get("variants", []) if v.get("user_id") == user["id"]]

    visible_matches = matches if user.get("is_paid") else matches[:3]
    locked_count = max(0, len(matches) - len(visible_matches))

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "resumes": resumes,
            "companies": state.get("companies", []),
            "jobs": state.get("jobs", []),
            "matches": visible_matches,
            "variants": variants,
            "locked_count": locked_count,
        },
    )


@app.post("/seed-companies")
def seed_companies(request: Request):
    state = store.load()
    _, redirect = _require_user(request, state)
    if redirect:
        return redirect

    state["companies"] = load_companies()
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/update-preferences")
def update_preferences(request: Request, preferred_locations: str = Form(...)):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    user["preferred_locations"] = [x.strip() for x in preferred_locations.split(",") if x.strip()]
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/upload-resume")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    file_bytes = await file.read()
    resume_id = str(uuid.uuid4())
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "resume")
    out_path = UPLOAD_DIR / f"{resume_id}_{safe_name}"
    out_path.write_bytes(file_bytes)

    parsed_text = _extract_text(safe_name, file_bytes)
    role_profile = infer_roles(parsed_text)

    state.setdefault("resumes", []).append(
        {
            "id": resume_id,
            "user_id": user["id"],
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
def ingest_jobs(request: Request, company_ids: list[str] = Form(default=[])):
    state = store.load()
    _, redirect = _require_user(request, state)
    if redirect:
        return redirect

    companies = [c for c in state.get("companies", []) if c["id"] in company_ids]
    jobs = ingest_jobs_for_companies(companies)
    state["jobs"] = jobs
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/run-matching")
def run_matching(request: Request, resume_id: str = Form(...)):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    resumes = [r for r in state.get("resumes", []) if r.get("user_id") == user["id"]]
    jobs = state.get("jobs", [])
    resume = next((r for r in resumes if r["id"] == resume_id), None)
    if not resume:
        return RedirectResponse(url="/", status_code=303)

    matches = rank_jobs(resume, jobs, state.get("companies", []), user.get("preferred_locations", []))
    for m in matches:
        m["user_id"] = user["id"]
    state["matches"] = [m for m in state.get("matches", []) if m.get("user_id") != user["id"]] + matches
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/tailor-top")
def tailor_top(request: Request, resume_id: str = Form(...), top_n: int = Form(default=3)):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    resumes = [r for r in state.get("resumes", []) if r.get("user_id") == user["id"]]
    resume = next((r for r in resumes if r["id"] == resume_id), None)
    if not resume:
        return RedirectResponse(url="/", status_code=303)

    top_matches = sorted([m for m in state.get("matches", []) if m.get("user_id") == user["id"]], key=lambda x: x["final_score"], reverse=True)[:top_n]
    variants = []
    for m in top_matches:
        job = next((j for j in state.get("jobs", []) if j["id"] == m["job_id"]), None)
        if not job:
            continue
        v = tailor_resume(resume, job, m)
        v["user_id"] = user["id"]
        variants.append(v)

    state["variants"] = [v for v in state.get("variants", []) if v.get("user_id") != user["id"]] + variants
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/track-event")
def track_event(request: Request, match_id: str = Form(...), event_type: str = Form(...)):
    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    state.setdefault("events", []).append(
        {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "match_id": match_id,
            "event_type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    for m in state.get("matches", []):
        if m["id"] == match_id and m.get("user_id") == user["id"]:
            m["status"] = event_type
    store.save(state)
    return RedirectResponse(url="/", status_code=303)
