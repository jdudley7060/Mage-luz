# Mage-Luz MVP — Seamless v2

Single-flow UX:
1) Set location preferences
2) Upload resume
3) Auto-run elite-company matching
4) Show top 3 free + paywall lock for rest
5) One-click tailored resume generation + DOCX/PDF export with role link

## Run

```bash
cd /Users/gurthang/.openclaw/workspace-mage-luz
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export SESSION_SECRET='change-me-long-random'
export COOKIE_SECURE=false  # true when behind HTTPS
# Optional LinkedIn OAuth:
# export LINKEDIN_CLIENT_ID='...'
# export LINKEDIN_CLIENT_SECRET='...'
# export LINKEDIN_REDIRECT_URI='http://localhost:8787/auth/linkedin/callback'

uvicorn app.main:app --reload --port 8787
```

Open: http://localhost:8787

## Security included
- Argon2 password hashing
- Session signing + secure-cookie toggle
- CSRF token checks on POST routes
- Login rate limiting/lockout

## Notes
- Elite jobs source list auto-loaded from provided company tiers.
- Matching adapts to the resume (finance, ops, SWE, marketing, accounting, etc.).
- LinkedIn sign-in is available once env vars are set locally.
