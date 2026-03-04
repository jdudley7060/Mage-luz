from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import requests

try:
    from scrapling import Fetcher
except Exception:  # pragma: no cover
    Fetcher = None

COMPANIES = {
    "S+": ["Anthropic", "OpenAI", "Google DeepMind", "Rentech", "TGS", "xAI", "Citadel Securities", "Jane Street", "HRT"],
    "S": ["Citadel", "D.E. Shaw", "Jump", "Optiver", "2s", "Tesla (Autopilot)", "Five Rings", "SpaceX"],
    "S-": ["IMC", "SIG", "DRW", "Akuna"],
    "A++": ["Databricks", "Netflix", "Anduril", "Google", "Meta", "Sierra AI", "Roblox"],
    "A+": ["Snowflake", "Waymo", "Stripe", "LinkedIn", "Figma", "Plaid", "Uber", "Airbnb", "Block (Cash App)", "Ramp", "Coinbase", "Nvidia", "AWS (Annapurna)", "Meta (Ads, M10N, MRS)", "Palantir", "Decagon"],
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
        "required_any": ["finance", "credit", "underwriting", "lending", "investment", "portfolio"],
    },
    "bizops_strategy_ops": {
        "keywords": ["operations", "strategy", "cross-functional", "process", "execution", "kpi", "go-to-market", "gtm"],
        "titles": ["BizOps", "Strategy & Operations", "Business Operations", "Chief of Staff", "Revenue Operations"],
        "required_any": ["operations", "strategy", "portfolio", "cross-functional", "execution"],
    },
    "product_strategy_gtm": {
        "keywords": ["product", "roadmap", "stakeholder", "launch", "market research", "customer", "pricing", "growth"],
        "titles": ["Product Strategy", "GTM Strategy", "Product Operations", "Product Manager"],
        "required_any": ["product", "gtm", "roadmap", "launch", "market"],
    },
    "software_engineering": {
        "keywords": ["python", "typescript", "react", "backend", "api", "distributed", "kubernetes", "software", "developer"],
        "titles": ["Software Engineer", "Backend Engineer", "Full Stack Engineer", "Solutions Engineer"],
        "required_any": ["software engineer", "engineer", "developer", "computer science", "full stack", "backend"],
    },
}


def load_companies() -> list[dict[str, Any]]:
    out = []
    for tier, names in COMPANIES.items():
        for name in names:
            out.append({"id": str(uuid.uuid4()), "name": name, "tier": tier})
    return out


def elite_companies(selected_names: list[str] | None = None) -> list[dict[str, Any]]:
    all_companies = load_companies()
    if selected_names:
        wanted = {x.strip().lower() for x in selected_names if x.strip()}
        filtered = [c for c in all_companies if c["name"].lower() in wanted]
        if filtered:
            return filtered
    tiers = ["S+", "S", "S-", "A++", "A+"]
    return [c for c in all_companies if c["tier"] in tiers]


def infer_roles(resume_text: str) -> dict[str, Any]:
    text = (resume_text or "").lower()
    lane_scores: dict[str, int] = {}
    for lane, spec in ROLE_LANES.items():
        raw = sum(1 for kw in spec["keywords"] if kw in text)
        required = spec.get("required_any", [])
        if required and not any(r in text for r in required):
            raw = 0
        lane_scores[lane] = raw

    ranked = sorted(lane_scores.items(), key=lambda x: x[1], reverse=True)
    top_lanes = [{"lane": lane, "score": score, "titles": ROLE_LANES[lane]["titles"]} for lane, score in ranked[:4] if score > 0]

    if not top_lanes:
        top_lanes = [
            {"lane": "software_engineering", "score": 1, "titles": ROLE_LANES["software_engineering"]["titles"]},
            {"lane": "bizops_strategy_ops", "score": 1, "titles": ROLE_LANES["bizops_strategy_ops"]["titles"]},
        ]

    tokens = re.findall(r"[a-zA-Z][a-zA-Z+.#-]{2,}", text)
    stop = {
        "about","across","with","from","this","that","their","through","using","over","into","under","and","the","for","your","have","will","were","been","where","when","while","into","none"
    }
    keywords = sorted({t.lower() for t in tokens if len(t) > 3 and t.lower() not in stop})[:120]

    return {"top_lanes": top_lanes, "keywords": keywords}


def _fetch_greenhouse(token: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        payload = r.json()
        out = []
        for j in payload.get("jobs", [])[:80]:
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


def _guess_career_urls(company_name: str) -> list[str]:
    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    base = slug.replace("-inc", "").replace("-corp", "")
    return [
        f"https://jobs.{base}.com",
        f"https://careers.{base}.com",
        f"https://{base}.com/careers",
        f"https://www.{base}.com/careers",
        f"https://{base}.com/jobs",
        f"https://www.{base}.com/jobs",
    ]


def _scrape_job_links_with_scrapling(company_name: str, role_profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if Fetcher is None:
        return []

    role_titles = [t.lower() for lane in (role_profile or {}).get("top_lanes", []) for t in lane.get("titles", [])]
    fetcher = Fetcher()
    links: list[dict[str, Any]] = []
    seen = set()

    for url in _guess_career_urls(company_name)[:2]:
        try:
            resp = fetcher.get(url, timeout=4)
            if getattr(resp, "status", 0) >= 400:
                continue
            for a in resp.css("a")[:500]:
                href = (a.attrib.get("href") or "").strip()
                txt = (a.text or "").strip()
                if not href:
                    continue
                full = href if href.startswith("http") else requests.compat.urljoin(url, href)
                low = f"{txt} {full}".lower()
                if not any(k in low for k in ["job", "career", "position", "opening", "apply", "greenhouse", "lever", "ashby"]):
                    continue
                if role_titles and not any(rt.lower() in low for rt in role_titles[:8]):
                    # keep some generic software/ops jobs anyway
                    if not any(x in low for x in ["engineer", "operations", "strategy", "product", "finance", "analyst"]):
                        continue
                key = full.split("?")[0]
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "id": str(uuid.uuid4()),
                        "external_job_id": key,
                        "title": txt[:180] if txt else "Role",
                        "location": "Unknown",
                        "description": "",
                        "apply_url": full,
                        "posted_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
        except Exception:
            continue
    return links[:60]


def ingest_jobs_for_companies(companies: list[dict[str, Any]], role_profile: dict[str, Any] | None = None, use_scrape: bool = True, max_companies: int = 18) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    top_lanes = (role_profile or {}).get("top_lanes", [])
    title_signals = [t.lower() for lane in top_lanes for t in lane.get("titles", [])]

    for c in companies[:max_companies]:
        cname = c["name"]
        token = GREENHOUSE_TOKENS.get(cname)

        fetched = _fetch_greenhouse(token) if token else []
        if not fetched and use_scrape:
            fetched = _scrape_job_links_with_scrapling(cname, role_profile)

        if top_lanes and fetched:
            filtered = [j for j in fetched if any(sig in (j.get("title", "").lower()) for sig in title_signals)]
            fetched = filtered[:15] if filtered else fetched[:10]

        for job in fetched:
            job["company_id"] = c["id"]
            job["company_name"] = cname
            job["tier"] = c["tier"]
        jobs.extend(fetched)

    return jobs


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a[:3000].lower(), b[:3000].lower()).ratio()


def rank_jobs(
    resume: dict[str, Any],
    jobs: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    preferred_locations: list[str] | None = None,
    preferred_industries: list[str] | None = None,
) -> list[dict[str, Any]]:
    profile = resume.get("role_profile", {})
    keywords = [k.lower() for k in profile.get("keywords", [])]
    top_lanes = profile.get("top_lanes", [])
    lane_titles = [t.lower() for lane in top_lanes for t in lane.get("titles", [])]

    pref = [p.lower() for p in (preferred_locations or [])]
    industry_pref = [i.lower() for i in (preferred_industries or [])]
    resume_text = resume.get("parsed_text", "")

    matches = []
    for j in jobs:
        title_raw = (j.get("title") or "").strip()
        if not title_raw or title_raw.lower() in {"none", "language", "careers", "applications"}:
            continue

        text = f"{j.get('title', '')} {j.get('description', '')}".lower()
        location_text = (j.get("location", "") or "").lower()

        skill_overlap = min(100, sum(1 for k in keywords[:70] if k in text) * 2)
        role_fit = 92 if any(t in text for t in lane_titles[:10]) else 52
        semantic_fit = round(_similarity(resume_text, text) * 100, 2)
        tier_weight = TIER_WEIGHT.get(j.get("tier", "B"), 60)
        location_fit = 90 if any(p in location_text for p in pref) or "remote" in location_text else 60
        link_quality = 85 if (j.get("apply_url") or "").startswith("http") else 50
        industry_fit = 80 if (industry_pref and any(i in text for i in industry_pref)) else (65 if not industry_pref else 45)

        final_score = round(
            0.30 * role_fit
            + 0.22 * skill_overlap
            + 0.20 * semantic_fit
            + 0.10 * tier_weight
            + 0.08 * location_fit
            + 0.05 * industry_fit
            + 0.05 * link_quality,
            2,
        )

        if final_score < 48:
            continue

        matches.append(
            {
                "id": str(uuid.uuid4()),
                "job_id": j["id"],
                "company_name": j.get("company_name"),
                "title": j.get("title") or "Role",
                "location": j.get("location") or "Unknown",
                "apply_url": j.get("apply_url", ""),
                "final_score": final_score,
                "status": "recommended",
            }
        )

    return sorted(matches, key=lambda x: x["final_score"], reverse=True)


def tailor_resume(resume: dict[str, Any], job: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    top_keywords = resume.get("role_profile", {}).get("keywords", [])[:12]
    resume_text = resume.get("parsed_text", "")

    lines = [ln.strip() for ln in resume_text.split("\n") if ln.strip()]
    wins = [ln for ln in lines if ("$" in ln or "mm" in ln.lower() or "%" in ln or "underw" in ln.lower() or "portfolio" in ln.lower())]
    wins = wins[:4] or lines[:4]

    bullets = [
        f"Repositioned experience for {job.get('title')} at {job.get('company_name')} with direct emphasis on {', '.join(top_keywords[:5]) or 'strategy, operations, and analytics'}.",
        *[f"Rewrote impact point: {w[:180]}" for w in wins[:3]],
        "Matched resume language to role requirements and responsibilities from the job description.",
    ]

    summary = f"Tailored for {job.get('title')} @ {job.get('company_name')}"
    rewritten_resume_text = "\n".join(
        [
            f"TARGET ROLE: {job.get('title')} ({job.get('company_name')})",
            "",
            "PROFESSIONAL SUMMARY (REWRITTEN):",
            f"Analytical operator with finance/strategy execution experience, tailored for {job.get('title')}. Background includes underwriting, portfolio management, cross-functional delivery, and data-driven decision support.",
            "",
            "TAILORED HIGHLIGHTS:",
            *[f"- {b}" for b in bullets],
            "",
            "ORIGINAL RESUME (REFERENCE):",
            resume_text[:8000],
        ]
    )

    return {
        "id": str(uuid.uuid4()),
        "job_id": job["id"],
        "job_title": job.get("title"),
        "company_name": job.get("company_name"),
        "score": match.get("final_score"),
        "apply_url": job.get("apply_url", ""),
        "summary": summary,
        "variant_text": "\n".join(["- " + b for b in bullets]),
        "rewritten_resume_text": rewritten_resume_text,
    }
