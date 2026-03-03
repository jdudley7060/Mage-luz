# Mage-Luz MVP (today build)

Web app implementing the 6-step MVP loop with login-gated features and a free-vs-pro recommendation wall:

1. Seed company tiers
2. Upload + parse resume (PDF/DOCX/TXT)
3. Ingest jobs for selected companies (Greenhouse where available + mock fallback)
4. Rank matches using weighted scoring formula
5. Generate tailored bullet variants for top jobs
6. Track funnel status (`saved/applied/interview/rejected`)

Access model:
- Login required for all core features
- Free plan sees top 3 matched jobs
- Remaining matches are gated behind Pro ($7.99/month, mock upgrade button in MVP)

Security hardening pass included:
- Argon2 password hashing (with legacy plaintext migration on first successful login)
- Session cookies with signed storage and configurable `COOKIE_SECURE`
- CSRF token checks on all POST forms
- Basic login rate limiting (temporary lockout after repeated failures)

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8787
```

Open: `http://localhost:8787`

## Notes

- Data is persisted to `data/db.json` for MVP speed.
- Greenhouse job ingestion is no-key and best-effort.
- If no live jobs are available, app creates mock postings so loop remains testable.
