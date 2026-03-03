# Mage-Luz MVP (today build)

Web app implementing the 6-step MVP loop:

1. Seed company tiers
2. Upload + parse resume (PDF/DOCX/TXT)
3. Ingest jobs for selected companies (Greenhouse where available + mock fallback)
4. Rank matches using weighted scoring formula
5. Generate tailored bullet variants for top jobs
6. Track funnel status (`saved/applied/interview/rejected`)

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
