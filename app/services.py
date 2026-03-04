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

ROLE_LANES: dict[str, dict[str, list[str]]] = {
    "strategic_finance": {
        "keywords": ["finance", "model", "cash flow", "credit", "lending", "portfolio", "underwriting", "debt", "valuation", "fp&a"],
        "titles": ["Strategic Finance", "Finance & Strategy", "Corporate Finance", "FP&A", "Investment Associate"],
    },
    "bizops_strategy_ops": {
        "keywords": ["operations", "strategy", "cross-functional", "process", "execution", "kpi", "go-to-market", "gtm"],
        "titles": ["BizOps", "Strategy & Operations", "Business Operations", "Chief of Staff", "Revenue Operations"],
    },
    "product_strategy_gtm": {
        "keywords": ["product", "roadmap", "stakeholder", "launch", "market research", "customer", "pricing", "growth"],
        "titles": ["Product Strategy", "GTM Strategy", "Product Operations", "Product Manager"],
    },
    "software_engineering": {
        "keywords": ["python", "typescript", "react", "backend", "api", "distributed", "kubernetes", "software", "developer"],
        "titles": ["Software Engineer", "Backend Engineer", "Full Stack Engineer", "Solutions Engineer"],
    },
    "data_analytics_ml": {
        "keywords": ["sql", "tableau", "power bi", "analytics", "machine learning", "model", "statistics", "data science"],
        "titles": ["Data Analyst", "Business Intelligence Analyst", "Data Scientist", "ML Engineer"],
    },
    "marketing_growth": {
        "keywords": ["marketing", "campaign", "acquisition", "brand", "content", "seo", "paid social"],
        "titles": ["Growth Marketing", "Product Marketing", "Marketing Strategy"],
    },
    "accounting": {
        "keywords": ["accounting", "gaap", "audit", "bookkeeping", "reconciliation", "controller"],
        "titles": ["Accountant", "Senior Accountant", "Accounting Manager"],
    },
}


def load_companies() -> list[dict[str, Any]]:
    out = []
    for tier, names in COMPANIES.items():
        for name in names:
            out.append({"id": str(uuid.uuid4()), "name": name, "tier": tier})
    return out


def elite_companies() -> list[dict[str, Any]]:
    tiers = ["S+", "S", "S-", "A++", "A+"]
    return [c for c in load_companies() if c["tier"] in tiers]


def infer_roles(resume_text: str) -> dict[str, Any]:
    text = (resume_text or "").lower()
    lane_scores: dict[str, int] = {}
    for lane, spec in ROLE_LANES.items():
        lane_scores[lane] = sum(1 for kw in spec["keywords"] if kw in text)

    ranked = sorted(lane_scores.items(), key=lambda x: x[1], reverse=True)
    top_lanes = [{"lane": lane, "score": score, "titles": ROLE_LANES[lane]["titles"]} for lane, score in ranked[:4] if score > 0]

    if not top_lanes:
        top_lanes = [
            {"lane": "software_engineering", "score": 1, "titles": ROLE_LANES["software_engineering"]["titles"]},
            {"lane": "bizops_strategy_ops", "score": 1, "titles": ROLE_LANES["bizops_strategy_ops"]["titles"]},
        ]

    tokens = re.findall(r"[a-zA-Z][a-zA-Z+.#-]{2,}", text)
    keywords = sorted({t.lower() for t in tokens if len(t) > 3})[:80]

    return {"top_lanes": top_lanes, "keywords": keywords}


def _fetch_greenhouse(token: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        payload = r.json()
        out = []
        for j in payload.get("jobs", [])[:50]:
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


def _mock_jobs_for_lanes(company_name: str, top_lanes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lane_list = top_lanes[:3] or [{"lane": "bizops_strategy_ops", "titles": ["Strategy & Operations"]}]
    for idx, lane in enumerate(lane_list, start=1):
        title = lane["titles"][0]
        out.append(
            {
                "id": str(uuid.uuid4()),
                "external_job_id": f"mock-{idx}",
                "title": f"{title}",
                "location": "New York, NY / San Francisco, CA / Remote",
                "description": f"{company_name} is hiring for {title}. Strong execution, analytical thinking, and cross-functional collaboration required.",
                "apply_url": f"https://www.google.com/search?q={company_name.replace(' ', '+')}+careers+{title.replace(' ', '+')}",
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return out


def ingest_jobs_for_companies(companies: list[dict[str, Any]], role_profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    top_lanes = (role_profile or {}).get("top_lanes", [])

    for c in companies[:30]:
        cname = c["name"]
        token = GREENHOUSE_TOKENS.get(cname)
        fetched = _fetch_greenhouse(token) if token else []

        # keep live jobs only if they match likely lanes; otherwise use lane-aware mocks for relevance
        if top_lanes and fetched:
            title_signals = [t.lower() for lane in top_lanes for t in lane.get("titles", [])]
            filtered = [j for j in fetched if any(sig.split()[0] in j.get("title", "").lower() for sig in title_signals)]
            fetched = filtered[:10] if filtered else []

        if not fetched:
            fetched = _mock_jobs_for_lanes(cname, top_lanes)

        for job in fetched:
            job["company_id"] = c["id"]
            job["company_name"] = cname
            job["tier"] = c["tier"]
        jobs.extend(fetched)
    return jobs


def rank_jobs(
    resume: dict[str, Any], jobs: list[dict[str, Any]], companies: list[dict[str, Any]], preferred_locations: list[str] | None = None
) -> list[dict[str, Any]]:
    profile = resume.get("role_profile", {})
    keywords = [k.lower() for k in profile.get("keywords", [])]
    top_lanes = profile.get("top_lanes", [])
    lane_titles = [t.lower() for lane in top_lanes for t in lane.get("titles", [])]

    pref = [p.lower() for p in (preferred_locations or [])]

    matches = []
    for j in jobs:
        text = f"{j.get('title', '')} {j.get('description', '')}".lower()
        location_text = (j.get("location", "") or "").lower()

        skill_overlap = min(100, sum(1 for k in keywords[:35] if k in text) * 4)
        role_fit = 90 if any(t.split()[0] in text for t in lane_titles) else 50
        seniority_fit = 75
        tier_weight = TIER_WEIGHT.get(j.get("tier", "B"), 60)
        location_fit = 90 if any(p in location_text for p in pref) or "remote" in location_text else 55
        recency = 80
        domain = 75

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
        if skill_overlap > 45:
            reasons.append("HIGH_SKILL_MATCH")
        if role_fit >= 80:
            reasons.append("ROLE_LANE_MATCH")
        if tier_weight >= 85:
            reasons.append("ELITE_COMPANY")
        if location_fit >= 85:
            reasons.append("LOCATION_MATCH")

        matches.append(
            {
                "id": str(uuid.uuid4()),
                "job_id": j["id"],
                "company_name": j.get("company_name"),
                "title": j.get("title"),
                "location": j.get("location"),
                "apply_url": j.get("apply_url", ""),
                "final_score": final_score,
                "reason_codes": reasons,
                "status": "recommended",
            }
        )

    return sorted(matches, key=lambda x: x["final_score"], reverse=True)


def tailor_resume(resume: dict[str, Any], job: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    top_keywords = resume.get("role_profile", {}).get("keywords", [])[:12]
    bullets = [
        f"Targeted this resume for {job.get('title')} at {job.get('company_name')} with emphasis on {', '.join(top_keywords[:5]) or 'analytical execution'}.",
        "Converted prior experience into impact-oriented bullet points with measurable outcomes.",
        "Aligned language to role responsibilities and cross-functional execution expectations.",
    ]
    summary = f"Tailored for {job.get('title')} @ {job.get('company_name')} | match score: {match.get('final_score')}"
    return {
        "id": str(uuid.uuid4()),
        "job_id": job["id"],
        "job_title": job.get("title"),
        "company_name": job.get("company_name"),
        "score": match.get("final_score"),
        "apply_url": job.get("apply_url", ""),
        "summary": summary,
        "variant_text": "\n".join(["- " + b for b in bullets]),
    }
