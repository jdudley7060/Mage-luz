from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

COMPANIES = {
    "S+": ["Anthropic", "OpenAI", "Google DeepMind", "Rentech", "TGS", "xAI", "Citadel Securities", "Jane Street", "HRT"],
    "S": ["Citadel", "D.E. Shaw", "Jump", "Optiver", "2s", "Tesla (Autopilot)", "Five Rings", "SpaceX"],
    "S-": ["IMC", "SIG", "DRW", "Akuna"],
    "A++": ["Databricks", "Netflix", "Anduril", "Google", "Meta", "Sierra AI", "Roblox"],
    "A+": ["Snowflake", "Waymo", "Stripe", "LinkedIn", "Figma", "Plaid", "Uber", "Airbnb", "Block (Cash App)", "Ramp", "Coinbase", "Nvidia", "AWS (Annapurna)", "Meta (Ads, M10N, MRS)", "Palantir", "Decagon"],
    "A": ["Notion", "Block (Square)", "Apple", "Doordash", "Datadog", "Robinhood", "MongoDB", "Google (GCP)", "Tesla", "Harvey", "Meta (Reality Labs)", "Pinterest"],
    "A-": ["Snap", "AWS", "Dropbox", "Google (YouTube)", "Rippling", "Upstart", "Vercel", "Cloudflare", "Crowdstrike", "Affirm", "Reddit", "Verkada", "Rubrik", "Lyft", "Instacart", "Twilio", "Okta", "Riot Games", "Circle", "TTD", "Pure Storage", "SoFi"],
    "B+": ["TikTok", "Discord", "Amazon", "Microsoft", "Bloomberg", "AMD", "Adobe", "Atlassian", "Docusign", "Box", "Intuit", "Hubspot"],
    "B": ["Duolingo", "Asana", "Spotify", "Epic Games", "Etsy", "Twitch", "AppLovin", "Paypal", "Workday"],
    "B-": ["Oracle", "Zoom", "IBM", "Salesforce", "c1", "eBay", "Shopify"],
}

TIER_WEIGHT = {"S+": 100, "S": 95, "S-": 90, "A++": 85, "A+": 80, "A": 75, "A-": 70, "B+": 65, "B": 60, "B-": 55}

GREENHOUSE_TOKENS = {
    "OpenAI": "openai",
    "Databricks": "databricks",
    "Stripe": "stripe",
    "Snowflake": "snowflake",
    "Cloudflare": "cloudflare",
    "Roblox": "roblox",
    "Notion": "notion",
    "Palantir": "palantir",
    "Figma": "figma",
    "Duolingo": "duolingo",
}


def load_companies() -> list[dict[str, Any]]:
    out = []
    for tier, names in COMPANIES.items():
        for name in names:
            out.append({"id": str(uuid.uuid4()), "name": name, "tier": tier})
    return out


def infer_roles(resume_text: str) -> dict[str, Any]:
    text = (resume_text or "").lower()
    role_keywords = {
        "software_engineering": ["python", "typescript", "react", "backend", "api", "distributed", "kubernetes"],
        "data_science_ml": ["machine learning", "pytorch", "tensorflow", "model", "nlp", "llm", "statistics"],
        "product_management": ["roadmap", "stakeholder", "launch", "kpi", "product strategy", "requirements"],
        "finance_quant": ["trading", "alpha", "risk", "portfolio", "quant", "pricing", "derivatives"],
        "operations_strategy": ["operations", "process", "vendor", "efficiency", "strategy", "execution"],
    }

    scores = {}
    for role, kws in role_keywords.items():
        scores[role] = sum(1 for k in kws if k in text)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = [{"role": r, "score": s} for r, s in ranked[:3] if s > 0]
    if not top:
        top = [{"role": "software_engineering", "score": 1}, {"role": "product_management", "score": 1}]

    tokens = re.findall(r"[a-zA-Z][a-zA-Z+.#-]{2,}", text)
    keywords = [t for t in tokens if len(t) > 3][:50]

    return {"top_roles": top, "keywords": sorted(set(keywords))[:40]}


def _fetch_greenhouse(token: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        payload = r.json()
        out = []
        for j in payload.get("jobs", [])[:40]:
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "external_job_id": str(j.get("id")),
                    "title": j.get("title", "Unknown role"),
                    "location": (j.get("location") or {}).get("name", "Unknown"),
                    "description": (j.get("content") or "")[:5000],
                    "apply_url": j.get("absolute_url", ""),
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return out
    except Exception:
        return []


def ingest_jobs_for_companies(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for c in companies:
        cname = c["name"]
        token = GREENHOUSE_TOKENS.get(cname)
        fetched = _fetch_greenhouse(token) if token else []
        if not fetched:
            fetched = [
                {
                    "id": str(uuid.uuid4()),
                    "external_job_id": "mock-1",
                    "title": f"{cname} — Software Engineer",
                    "location": "San Francisco, CA",
                    "description": "Build product features, ship production code, collaborate across teams.",
                    "apply_url": "",
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "id": str(uuid.uuid4()),
                    "external_job_id": "mock-2",
                    "title": f"{cname} — Product Manager",
                    "location": "Remote (US)",
                    "description": "Own roadmap, drive launches, define success metrics.",
                    "apply_url": "",
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                },
            ]

        for job in fetched:
            job["company_id"] = c["id"]
            job["company_name"] = cname
            job["tier"] = c["tier"]
        jobs.extend(fetched)
    return jobs


def rank_jobs(resume: dict[str, Any], jobs: list[dict[str, Any]], companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile = resume.get("role_profile", {})
    keywords = [k.lower() for k in profile.get("keywords", [])]
    top_roles = [r["role"] for r in profile.get("top_roles", [])]

    matches = []
    for j in jobs:
        text = f"{j.get('title','')} {j.get('description','')}".lower()
        skill_overlap = min(100, sum(1 for k in keywords[:25] if k in text) * 5)
        role_fit = 80 if any(r.split("_")[0] in text for r in top_roles) else 55
        seniority_fit = 70
        tier_weight = TIER_WEIGHT.get(j.get("tier", "B"), 60)
        location_fit = 85 if any(x in (j.get("location", "").lower()) for x in ["remote", "san francisco", "new york"]) else 60
        recency = 80
        domain = 70
        final_score = round(
            0.35 * role_fit
            + 0.20 * skill_overlap
            + 0.15 * seniority_fit
            + 0.10 * tier_weight
            + 0.10 * location_fit
            + 0.05 * recency
            + 0.05 * domain,
            2,
        )
        reasons = []
        if skill_overlap > 55:
            reasons.append("HIGH_SKILL_MATCH")
        if role_fit >= 75:
            reasons.append("STRONG_ROLE_SIMILARITY")
        if tier_weight >= 85:
            reasons.append("TOP_TIER_TARGET")

        matches.append(
            {
                "id": str(uuid.uuid4()),
                "job_id": j["id"],
                "company_name": j.get("company_name"),
                "title": j.get("title"),
                "location": j.get("location"),
                "final_score": final_score,
                "role_fit": role_fit,
                "skill_overlap": skill_overlap,
                "seniority_fit": seniority_fit,
                "tier_weight": tier_weight,
                "location_fit": location_fit,
                "recency_score": recency,
                "domain_fit": domain,
                "reason_codes": reasons,
                "status": "recommended",
            }
        )

    return sorted(matches, key=lambda x: x["final_score"], reverse=True)


def tailor_resume(resume: dict[str, Any], job: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    top_keywords = resume.get("role_profile", {}).get("keywords", [])[:8]
    bullets = [
        f"Aligned experience to {job.get('title')} requirements with focus on: {', '.join(top_keywords[:4]) or 'core delivery'}.",
        "Quantified impact in prior roles using outcome metrics and execution speed.",
        f"Highlighted domain fit for {job.get('company_name')} and cross-functional collaboration.",
    ]
    return {
        "id": str(uuid.uuid4()),
        "job_id": job["id"],
        "job_title": job.get("title"),
        "company_name": job.get("company_name"),
        "score": match.get("final_score"),
        "variant_text": "\n".join(["- " + b for b in bullets]),
    }
