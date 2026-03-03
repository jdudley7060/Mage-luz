from __future__ import annotations

import io
import os
import re
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
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

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ph = PasswordHasher()

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8787/auth/linkedin/callback")

app = FastAPI(title="Mage-Luz MVP")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 24,
)
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


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _check_csrf(request: Request, token: str) -> bool:
    expected = request.session.get("csrf_token")
    return bool(expected and token and secrets.compare_digest(expected, token))


def _client_key(request: Request, email: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{email.lower()}::{ip}"


def _is_rate_limited(state: dict, key: str) -> tuple[bool, int]:
    record = (state.get("login_attempts") or {}).get(key, {"count": 0, "locked_until": 0})
    now = int(time.time())
    if record.get("locked_until", 0) > now:
        return True, int(record["locked_until"] - now)
    return False, 0


def _record_failed_attempt(state: dict, key: str) -> None:
    attempts = state.setdefault("login_attempts", {})
    record = attempts.get(key, {"count": 0, "locked_until": 0})
    record["count"] = record.get("count", 0) + 1
    if record["count"] >= 5:
        record["locked_until"] = int(time.time()) + 15 * 60
        record["count"] = 0
    attempts[key] = record


def _clear_attempts(state: dict, key: str) -> None:
    attempts = state.setdefault("login_attempts", {})
    attempts.pop(key, None)


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


def _render_login(request: Request, error: str | None = None):
    linkedin_ready = bool(LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "csrf_token": _csrf_token(request),
            "linkedin_ready": linkedin_ready,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _render_login(request)


@app.get("/auth/linkedin/start")
def linkedin_start(request: Request):
    if not (LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI):
        return _render_login(request, "LinkedIn OAuth is not configured yet.")

    state_token = secrets.token_urlsafe(24)
    request.session["linkedin_oauth_state"] = state_token

    query = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state_token,
        "scope": "openid profile email",
    }
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(query)
    return RedirectResponse(url=url, status_code=302)


@app.get("/auth/linkedin/callback")
def linkedin_callback(request: Request, code: str = "", state: str = ""):
    expected_state = request.session.get("linkedin_oauth_state")
    if not expected_state or not state or not secrets.compare_digest(expected_state, state):
        return _render_login(request, "LinkedIn login failed (state mismatch).")

    try:
        token_resp = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LINKEDIN_REDIRECT_URI,
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            timeout=20,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return _render_login(request, "LinkedIn login failed (missing access token).")

        userinfo = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        userinfo.raise_for_status()
        profile = userinfo.json()
    except Exception:
        return _render_login(request, "LinkedIn login failed during token/profile exchange.")

    linkedin_sub = str(profile.get("sub") or profile.get("id") or "")
    email = profile.get("email") or ""
    if not linkedin_sub:
        return _render_login(request, "LinkedIn login failed (missing profile id).")

    state_db = store.load()
    user = next((u for u in state_db.get("users", []) if u.get("linkedin_sub") == linkedin_sub), None)
    if not user and email:
        user = next((u for u in state_db.get("users", []) if u.get("email", "").lower() == email.lower()), None)

    if not user:
        user = {
            "id": str(uuid.uuid4()),
            "email": email or f"linkedin_{linkedin_sub}@local",
            "password_hash": "",
            "linkedin_sub": linkedin_sub,
            "is_paid": False,
            "plan": "free",
            "preferred_locations": ["New York", "San Francisco"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        state_db.setdefault("users", []).append(user)
    else:
        user["linkedin_sub"] = linkedin_sub

    store.save(state_db)
    request.session["user_id"] = user["id"]
    request.session.pop("linkedin_oauth_state", None)
    return RedirectResponse(url="/", status_code=303)


@app.post("/register")
def register(
    request: Request,
    csrf_token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    preferred_locations: str = Form(default="New York,San Francisco"),
):
    if not _check_csrf(request, csrf_token):
        return _render_login(request, "Session expired. Refresh and try again.")

    state = store.load()
    if any(u["email"].lower() == email.lower() for u in state.get("users", [])):
        return _render_login(request, "Email already exists.")

    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": ph.hash(password),
        "linkedin_sub": "",
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
def login(request: Request, csrf_token: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return _render_login(request, "Session expired. Refresh and try again.")

    state = store.load()
    key = _client_key(request, email)
    limited, wait_s = _is_rate_limited(state, key)
    if limited:
        return _render_login(request, f"Too many attempts. Try again in {wait_s}s.")

    user = next((u for u in state.get("users", []) if u["email"].lower() == email.lower()), None)
    if not user:
        _record_failed_attempt(state, key)
        store.save(state)
        return _render_login(request, "Invalid credentials.")

    ok = False
    if user.get("password_hash"):
        try:
            ok = ph.verify(user["password_hash"], password)
        except VerifyMismatchError:
            ok = False
    elif user.get("password") == password:
        user["password_hash"] = ph.hash(password)
        user.pop("password", None)
        ok = True

    if not ok:
        _record_failed_attempt(state, key)
        store.save(state)
        return _render_login(request, "Invalid credentials.")

    _clear_attempts(state, key)
    store.save(state)
    request.session["user_id"] = user["id"]
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.post("/mock-upgrade")
def mock_upgrade(request: Request, csrf_token: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

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
            "csrf_token": _csrf_token(request),
        },
    )


@app.post("/seed-companies")
def seed_companies(request: Request, csrf_token: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

    state = store.load()
    _, redirect = _require_user(request, state)
    if redirect:
        return redirect

    state["companies"] = load_companies()
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/update-preferences")
def update_preferences(request: Request, csrf_token: str = Form(...), preferred_locations: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    user["preferred_locations"] = [x.strip() for x in preferred_locations.split(",") if x.strip()]
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/upload-resume")
async def upload_resume(request: Request, csrf_token: str = Form(...), file: UploadFile = File(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

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
def ingest_jobs(request: Request, csrf_token: str = Form(...), company_ids: list[str] = Form(default=[])):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

    state = store.load()
    _, redirect = _require_user(request, state)
    if redirect:
        return redirect

    companies = [c for c in state.get("companies", []) if c["id"] in company_ids]
    state["jobs"] = ingest_jobs_for_companies(companies)
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/run-matching")
def run_matching(request: Request, csrf_token: str = Form(...), resume_id: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

    state = store.load()
    user, redirect = _require_user(request, state)
    if redirect:
        return redirect

    resumes = [r for r in state.get("resumes", []) if r.get("user_id") == user["id"]]
    resume = next((r for r in resumes if r["id"] == resume_id), None)
    if not resume:
        return RedirectResponse(url="/", status_code=303)

    matches = rank_jobs(resume, state.get("jobs", []), state.get("companies", []), user.get("preferred_locations", []))
    for m in matches:
        m["user_id"] = user["id"]
    state["matches"] = [m for m in state.get("matches", []) if m.get("user_id") != user["id"]] + matches
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/tailor-top")
def tailor_top(request: Request, csrf_token: str = Form(...), resume_id: str = Form(...), top_n: int = Form(default=3)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

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
        if job:
            v = tailor_resume(resume, job, m)
            v["user_id"] = user["id"]
            variants.append(v)

    state["variants"] = [v for v in state.get("variants", []) if v.get("user_id") != user["id"]] + variants
    store.save(state)
    return RedirectResponse(url="/", status_code=303)


@app.post("/track-event")
def track_event(request: Request, csrf_token: str = Form(...), match_id: str = Form(...), event_type: str = Form(...)):
    if not _check_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=303)

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
