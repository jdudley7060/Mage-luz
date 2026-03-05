from __future__ import annotations

import html
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

# Scope-locked: Top-30 only
TOP30_COMPANIES = [
    "Anthropic",
    "OpenAI",
    "Google DeepMind",
    "Rentech",
    "TGS",
    "xAI",
    "Citadel Securities",
    "Jane Street",
    "HRT",
    "Citadel",
    "D.E. Shaw",
    "Jump",
    "Optiver",
    "Two Sigma",
    "Tesla (Autopilot)",
    "Five Rings",
    "SpaceX",
    "IMC",
    "SIG",
    "DRW",
    "Akuna",
    "Databricks",
    "Netflix",
    "Anduril",
    "Google",
    "Meta",
    "Sierra AI",
    "Roblox",
    "Snowflake",
    "Waymo",
]

COMPANY_TIERS = {
    "Anthropic": "S+",
    "OpenAI": "S+",
    "Google DeepMind": "S+",
    "Rentech": "S+",
    "TGS": "S+",
    "xAI": "S+",
    "Citadel Securities": "S+",
    "Jane Street": "S+",
    "HRT": "S+",
    "Citadel": "S",
    "D.E. Shaw": "S",
    "Jump": "S",
    "Optiver": "S",
    "Two Sigma": "S",
    "Tesla (Autopilot)": "S",
    "Five Rings": "S",
    "SpaceX": "S",
    "IMC": "S-",
    "SIG": "S-",
    "DRW": "S-",
    "Akuna": "S-",
    "Databricks": "A++",
    "Netflix": "A++",
    "Anduril": "A++",
    "Google": "A++",
    "Meta": "A++",
    "Sierra AI": "A++",
    "Roblox": "A++",
    "Snowflake": "A+",
    "Waymo": "A+",
}

TIER_WEIGHT = {"S+": 100, "S": 95, "S-": 90, "A++": 85, "A+": 80, "A": 75, "A-": 70, "B+": 65, "B": 60, "B-": 55}

COMPANY_INDUSTRIES: dict[str, set[str]] = {
    "Anthropic": {"ai", "saas"},
    "OpenAI": {"ai", "saas"},
    "Google DeepMind": {"ai"},
    "Rentech": {"fintech"},
    "TGS": {"fintech"},
    "xAI": {"ai"},
    "Citadel Securities": {"fintech"},
    "Jane Street": {"fintech"},
    "HRT": {"fintech"},
    "Citadel": {"fintech"},
    "D.E. Shaw": {"fintech"},
    "Jump": {"fintech"},
    "Optiver": {"fintech"},
    "Two Sigma": {"fintech", "ai"},
    "Tesla (Autopilot)": {"ai", "consumer"},
    "Five Rings": {"fintech"},
    "SpaceX": {"consumer"},
    "IMC": {"fintech"},
    "SIG": {"fintech"},
    "DRW": {"fintech"},
    "Akuna": {"fintech"},
    "Databricks": {"ai", "saas"},
    "Netflix": {"consumer", "saas"},
    "Anduril": {"ai"},
    "Google": {"ai", "consumer", "saas"},
    "Meta": {"ai", "consumer", "saas"},
    "Sierra AI": {"ai", "saas"},
    "Roblox": {"consumer", "saas"},
    "Snowflake": {"saas", "ai"},
    "Waymo": {"ai", "consumer"},
}

GREENHOUSE_TOKENS = {
    "OpenAI": "openai",
    "Databricks": "databricks",
    "Snowflake": "snowflake",
    "Roblox": "roblox",
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


JOB_URL_SIGNALS = ["/jobs", "/careers/jobs", "job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "ashbyhq.com", "workdayjobs.com"]
JOB_TEXT_SIGNALS = ["engineer", "analyst", "manager", "research", "operations", "strategy", "product", "finance", "scientist", "developer", "associate", "intern"]
NON_JOB_TEXT = {"none", "language", "careers", "applications", "search", "home", "privacy", "terms", "cookie", "accessibility"}


def load_companies() -> list[dict[str, Any]]:
    return [{"id": str(uuid.uuid4()), "name": name, "tier": COMPANY_TIERS[name]} for name in TOP30_COMPANIES]


def elite_companies(selected_names: list[str] | None = None) -> list[dict[str, Any]]:
    all_companies = load_companies()
    if selected_names:
        wanted = {x.strip().lower() for x in selected_names if x.strip()}
        filtered = [c for c in all_companies if c["name"].lower() in wanted]
        if filtered:
            return filtered
    return all_companies


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
        "about", "across", "with", "from", "this", "that", "their", "through", "using", "over", "into", "under", "and", "the", "for", "your", "have", "will", "were", "been", "where", "when", "while", "into", "none"
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
        for j in payload.get("jobs", [])[:120]:
            title = (j.get("title") or "").strip()
            if not title or title.lower() in NON_JOB_TEXT:
                continue
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "external_job_id": str(j.get("id")),
                    "title": title,
                    "location": (j.get("location") or {}).get("name", "Unknown"),
                    "description": (j.get("content") or "")[:8000],
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


def _clean_html_text(raw: str) -> str:
    raw = html.unescape(raw or "")
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _is_probable_job_link(text: str, url: str) -> bool:
    low = f"{text} {url}".lower()
    if any(bad in low for bad in ["privacy", "cookie", "terms", "accessibility", "talent-community", "newsletter", "office", "blog", "events", "disclosures"]):
        return False
    if any(sig in low for sig in JOB_URL_SIGNALS):
        return True
    return any(sig in low for sig in JOB_TEXT_SIGNALS)


def _title_from_anchor(text: str, href: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if t and t.lower() not in NON_JOB_TEXT and len(t) > 3:
        return t[:180]
    slug = href.rstrip("/").split("/")[-1].split("?")[0]
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\d+", "", slug).strip()
    if slug and slug.lower() not in NON_JOB_TEXT:
        return slug.title()[:180]
    return "Role"


def _scrape_job_links_with_scrapling(company_name: str, role_profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if Fetcher is None:
        return []

    role_titles = [t.lower() for lane in (role_profile or {}).get("top_lanes", []) for t in lane.get("titles", [])]
    fetcher = Fetcher()
    links: list[dict[str, Any]] = []
    seen = set()

    for url in _guess_career_urls(company_name)[:3]:
        try:
            resp = fetcher.get(url, timeout=4)
            if getattr(resp, "status", 0) >= 400:
                continue
            for a in resp.css("a")[:700]:
                href = (a.attrib.get("href") or "").strip()
                txt = (a.text or "").strip()
                if not href:
                    continue
                full = href if href.startswith("http") else requests.compat.urljoin(url, href)
                full_key = full.split("?")[0]
                if full_key in seen:
                    continue
                if not _is_probable_job_link(txt, full):
                    continue

                low = f"{txt} {full}".lower()
                if role_titles and not any(rt in low for rt in role_titles[:8]):
                    if not any(x in low for x in JOB_TEXT_SIGNALS):
                        continue

                title = _title_from_anchor(txt, full)
                if title.lower() in NON_JOB_TEXT:
                    continue
                seen.add(full_key)
                links.append(
                    {
                        "id": str(uuid.uuid4()),
                        "external_job_id": full_key,
                        "title": title,
                        "location": "Unknown",
                        "description": "",
                        "apply_url": full,
                        "posted_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
        except Exception:
            continue
    return links[:80]


def ingest_jobs_for_companies(companies: list[dict[str, Any]], role_profile: dict[str, Any] | None = None, use_scrape: bool = True, max_companies: int = 30) -> list[dict[str, Any]]:
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
            fetched = filtered[:20] if filtered else fetched[:12]

        for job in fetched:
            job["company_id"] = c["id"]
            job["company_name"] = cname
            job["tier"] = c["tier"]
        jobs.extend(fetched)

    return jobs


def industries_for_company(company_name: str) -> set[str]:
    return COMPANY_INDUSTRIES.get(company_name, set())


def filter_jobs_by_industries(jobs: list[dict[str, Any]], preferred_industries: list[str] | None) -> list[dict[str, Any]]:
    prefs = {x.lower().strip() for x in (preferred_industries or []) if x.strip()}
    if not prefs:
        return jobs
    filtered = []
    for j in jobs:
        tags = industries_for_company(j.get("company_name", ""))
        if tags & prefs:
            filtered.append(j)
    return filtered


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a[:3000].lower(), b[:3000].lower()).ratio()


def _extract_jd_requirements(description: str) -> list[str]:
    plain = _clean_html_text(description)
    parts = re.split(r"[\n\.;•]|\s{2,}", plain)
    reqs = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        low = s.lower()
        if any(k in low for k in ["experience", "required", "must", "proficien", "strong", "ability", "familiar"]):
            reqs.append(s[:140])
    dedup = []
    seen = set()
    for r in reqs:
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup[:12]


def rank_jobs(
    resume: dict[str, Any],
    jobs: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    preferred_locations: list[str] | None = None,
    preferred_industries: list[str] | None = None,
    selected_role: str = "",
) -> list[dict[str, Any]]:
    profile = resume.get("role_profile", {})
    keywords = [k.lower() for k in profile.get("keywords", [])]
    top_lanes = profile.get("top_lanes", [])
    lane_titles = [t.lower() for lane in top_lanes for t in lane.get("titles", [])]

    pref = [p.lower() for p in (preferred_locations or [])]
    industry_pref = [i.lower() for i in (preferred_industries or [])]
    selected_role_low = (selected_role or "").lower().strip()
    selected_role_tokens = [t for t in re.findall(r"[a-zA-Z]{3,}", selected_role_low) if t not in {"and", "the", "for", "with", "role"}]
    resume_text = resume.get("parsed_text", "")

    matches = []
    for j in jobs:
        title_raw = (j.get("title") or "").strip()
        if not title_raw or title_raw.lower() in NON_JOB_TEXT:
            continue

        description = j.get("description", "")
        text = f"{title_raw} {description}".lower()
        location_text = (j.get("location", "") or "").lower()

        reqs = _extract_jd_requirements(description)
        req_hits = sum(1 for r in reqs if _similarity(r, resume_text) > 0.18 or any(k in resume_text.lower() for k in re.findall(r"[a-zA-Z]{4,}", r.lower())[:3]))
        requirement_score = round(100 * (req_hits / max(1, len(reqs))), 2)

        skill_overlap = min(100, sum(1 for k in keywords[:90] if k in text) * 2)
        lane_role_fit = 95 if any(t in text for t in lane_titles[:12]) else 40
        selected_role_fit = 70
        if selected_role_tokens:
            selected_role_fit = 98 if all(tok in text for tok in selected_role_tokens[:4]) else (85 if any(tok in text for tok in selected_role_tokens[:4]) else 28)
        role_fit = round(0.55 * lane_role_fit + 0.45 * selected_role_fit, 2)

        semantic_fit = round(_similarity(resume_text, text) * 100, 2)
        title_similarity = round(max([_similarity(title_raw, t) for t in lane_titles[:12]] + [0.0]) * 100, 2)
        if selected_role_low:
            title_similarity = round(max(title_similarity, _similarity(title_raw.lower(), selected_role_low) * 100), 2)

        tier_weight = TIER_WEIGHT.get(j.get("tier", "B"), 60)
        location_fit = 94 if any(p in location_text for p in pref) or "remote" in location_text else (58 if not pref else 25)

        industry_tags = industries_for_company(j.get("company_name", ""))
        industry_fit = 98 if (industry_pref and (industry_tags & set(industry_pref))) else (65 if not industry_pref else 12)

        final_score = round(
            0.34 * role_fit
            + 0.18 * skill_overlap
            + 0.18 * semantic_fit
            + 0.14 * requirement_score
            + 0.05 * tier_weight
            + 0.05 * location_fit
            + 0.03 * title_similarity
            + 0.03 * industry_fit,
            2,
        )

        if final_score < 48:
            continue

        matches.append(
            {
                "id": str(uuid.uuid4()),
                "job_id": j["id"],
                "company_name": j.get("company_name"),
                "title": title_raw,
                "location": j.get("location") or "Unknown",
                "apply_url": j.get("apply_url", ""),
                "final_score": final_score,
                "requirement_score": requirement_score,
                "status": "recommended",
            }
        )

    return sorted(matches, key=lambda x: x["final_score"], reverse=True)


def _split_resume_sections(resume_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"GENERAL": []}
    current = "GENERAL"
    for raw in resume_text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.isupper() and len(line) < 45:
            current = line
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _best_resume_evidence(requirement: str, resume_lines: list[str]) -> str:
    best = ""
    best_score = 0.0
    for ln in resume_lines:
        s = _similarity(requirement, ln)
        if s > best_score:
            best_score = s
            best = ln
    return best[:180] if best_score > 0.14 else "No direct evidence line found; keep as target emphasis."


def tailor_resume(resume: dict[str, Any], job: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    top_keywords = resume.get("role_profile", {}).get("keywords", [])[:12]
    resume_text = resume.get("parsed_text", "")
    sections = _split_resume_sections(resume_text)

    jd_requirements = _extract_jd_requirements(job.get("description", ""))[:8]
    exp_lines = sections.get("PROFESSIONAL EXPERIENCE", []) or sections.get("GENERAL", [])

    mapped = []
    for req in jd_requirements[:6]:
        mapped.append((req, _best_resume_evidence(req, exp_lines)))

    wins = [ln for ln in exp_lines if ("$" in ln or "mm" in ln.lower() or "%" in ln or "underw" in ln.lower() or "portfolio" in ln.lower())]
    wins = wins[:5] or exp_lines[:5]

    bullets = [
        f"Repositioned experience for {job.get('title')} at {job.get('company_name')} with emphasis on {', '.join(top_keywords[:5]) or 'strategy, operations, and analytics'}.",
        *[f"{w[:190]}" for w in wins[:3]],
        "Language aligned to explicit role requirements and business outcomes in the posting.",
    ]

    summary = f"Tailored for {job.get('title')} @ {job.get('company_name')}"

    education_block = "\n".join(sections.get("EDUCATION", [])[:8])
    skills_block = "\n".join((sections.get("SKILLS & INTERESTS", []) or sections.get("SKILLS", []))[:12])
    experience_block = "\n".join([f"- {b}" for b in bullets])
    req_map_block = "\n".join([f"- JD: {req}\n  Resume evidence: {ev}" for req, ev in mapped]) or "- No structured requirements parsed from JD; used title + description signals."

    rewritten_resume_text = "\n".join(
        [
            f"TARGET ROLE: {job.get('title')} ({job.get('company_name')})",
            f"ROLE LINK: {job.get('apply_url', '')}",
            "",
            "PROFESSIONAL SUMMARY",
            f"Analytical operator with strong finance/strategy and execution background, tailored for {job.get('title')}. Experience includes underwriting, portfolio management, cross-functional execution, and data-backed decision-making.",
            "",
            "REQUIREMENT MAPPING",
            req_map_block,
            "",
            "PROFESSIONAL EXPERIENCE (TAILORED)",
            experience_block,
            "",
            "EDUCATION",
            education_block or "(pulled from original resume)",
            "",
            "SKILLS",
            skills_block or ", ".join(top_keywords[:10]),
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
