#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

python -m py_compile app/main.py app/services.py app/storage.py
python - <<'PY'
from app.services import load_companies, ingest_jobs_for_companies, NON_JOB_TEXT
companies = load_companies()
jobs = ingest_jobs_for_companies(companies, role_profile=None, use_scrape=True, max_companies=len(companies))
valid = [j for j in jobs if (j.get('title') or '').strip().lower() not in NON_JOB_TEXT]
print(f"Top30 companies: {len(companies)}")
print(f"Jobs fetched: {len(jobs)}")
print(f"Valid jobs: {len(valid)}")
print("PASS" if len(companies)==30 and len(valid)>0 else "FAIL")
PY

echo "Smoke test done."
