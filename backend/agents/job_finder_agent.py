"""
Job Finder Agent — fetches real jobs via JSearch API (RapidAPI), falls back to Claude generation.
"""
import json
import re
import uuid
import os
import httpx
from services.claude_service import complete_claude_json
from loguru import logger

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

SYSTEM_JOB_GENERATOR = """You are India's most comprehensive real-time job database. Generate EXACTLY 8 realistic, highly relevant job listings for the given search query. The current year is 2026.

CRITICAL RULES:
1. ALL 8 jobs MUST match the search query directly (e.g., "ecommerce manager" → E-Commerce Manager, Category Manager, Marketplace Manager, etc.)
2. Match to REAL companies in that domain:
   - Ecommerce: Amazon, Flipkart, Meesho, Myntra, Nykaa, Snapdeal, Ajio, Jiomart, Tata Digital, Purplle
   - Fintech: Razorpay, CRED, PhonePe, Paytm, Zepto, BharatPe, Jupiter, Fi Money
   - Data/AI: Mu Sigma, Tiger Analytics, Fractal, Latent View, ThoughtWorks, Sigmoid
   - SaaS: Freshworks, Zoho, Chargebee, Clevertap, WebEngage, MoEngage
   - FAANG-India: Google, Microsoft, Amazon, Meta, Uber, Walmart Global Tech
   - Consulting: McKinsey, BCG, Bain, Deloitte, EY, PwC, Accenture
3. Use the SPECIFIED location if given; otherwise use Bangalore/Hyderabad/Mumbai/Delhi/Pune
4. Use specified salary range if given; otherwise use market-accurate ranges
5. Each job needs 5-6 skills SPECIFIC to that role
6. Descriptions must be detailed and realistic (2-3 sentences with team size, tech stack, impact)
7. PORTAL URLS must be REAL search URLs — not homepages:
   - LinkedIn: "https://www.linkedin.com/jobs/search/?keywords={URL-encoded+job+title}&location={URL-encoded+location}"
   - Naukri: "https://www.naukri.com/{job-title-slug}-jobs-in-{location-slug}"
   - Indeed: "https://in.indeed.com/jobs?q={URL-encoded+job+title}&l={URL-encoded+location}"
   - Glassdoor: "https://www.glassdoor.co.in/Job/{location-slug}-{job-title-slug}-jobs-SRCH_IL.0,10_IC{id}_KO{n}.htm"
   - Instahyre: "https://www.instahyre.com/jobs/?q={job-title}"
8. posted_date MUST be in 2026 (e.g., "2026-05-28", "2026-05-25") — NEVER use 2024.
9. url field: construct the most relevant direct application/search URL for that specific company+role.

SALARY BENCHMARKS (INR per year):
- Entry (0-2yr): 4L-12L | Mid (2-5yr): 12L-25L | Senior (5-8yr): 25L-50L | Lead (8+yr): 45L-80L | Director: 80L-1.5Cr

Output ONLY a valid JSON array with EXACTLY 8 jobs (no other text, no markdown):
[
  {
    "id": "job_<unique_6char>",
    "title": "exact job title closely matching the search query",
    "company": "company name",
    "company_logo": "https://logo.clearbit.com/<domain.com>",
    "location": "<City>, India",
    "remote": "Remote|Hybrid|On-site",
    "salary_min": <integer INR>,
    "salary_max": <integer INR>,
    "salary_currency": "INR",
    "experience_required": "X-Y years",
    "posted_date": "2026-05-<DD between 10-31>",
    "description": "2-3 sentence specific description with actual tech/product context",
    "skills": ["role-specific skill 1", "skill 2", "skill 3", "skill 4", "skill 5"],
    "portal": "LinkedIn|Naukri|Indeed|Glassdoor|Instahyre|AngelList",
    "portal_url": "<real search URL as described in rule 7 above>",
    "url": "<real search/listing URL for this specific company+role>",
    "job_type": "Full-time",
    "seniority": "Entry|Mid|Senior|Lead|Principal|Director",
    "match_score": <integer 72-95>
  }
]"""

SYSTEM_JOB_RANKER = """You are a career advisor. Score each job by how well it fits this specific candidate.

For EACH job in the input, return one object:
{
  "id": "<same id from input>",
  "match_score": <integer 40-95 — use the FULL range, differentiate clearly>,
  "why_match": "One sentence explaining the fit",
  "apply_priority": "high|medium|low"
}

Scoring rules:
- 85-95: Near-perfect skills + seniority + domain match
- 70-84: Good match, minor gaps
- 55-69: Partial match, notable skill gaps
- 40-54: Weak match, different domain or level

IMPORTANT: Do NOT cluster all scores in a narrow range. Differentiate based on real fit.
Return ONLY a JSON array, no other text."""

SYSTEM_RESUME_JOB_GENERATOR = """You are India's most precise job-matching AI. A candidate's complete resume profile is provided. Generate EXACTLY 8 job listings that this specific candidate would be an excellent match for.

CRITICAL MATCHING RULES:
1. Read the candidate's skills, job titles, domain, and experience CAREFULLY
2. Generate jobs that match their EXACT background — never suggest roles outside their domain
3. Match seniority precisely: junior profiles → entry/mid roles; senior profiles → senior/lead roles
4. Use the candidate's preferred location (or Bangalore/Hyderabad if unspecified)
5. Skills in each job listing MUST substantially overlap with the candidate's actual skills
6. Use companies that ACTIVELY hire for this profile type in India (2026)
7. Set salary ranges based on the candidate's years of experience
8. All portal_url fields must be valid real search URLs for the role
9. posted_date MUST be in 2026

QUALITY BAR: Each job must feel hand-picked. If the candidate is a Python ML Engineer, all 8 jobs should be ML/AI/Data roles at tech companies — never generic software roles.

Output ONLY a valid JSON array with EXACTLY 8 jobs (no other text, no markdown):
[
  {
    "id": "job_<unique_6char>",
    "title": "specific job title matching candidate profile",
    "company": "company name",
    "company_logo": "https://logo.clearbit.com/<domain.com>",
    "location": "<City>, India",
    "remote": "Remote|Hybrid|On-site",
    "salary_min": <integer INR>,
    "salary_max": <integer INR>,
    "salary_currency": "INR",
    "experience_required": "X-Y years",
    "posted_date": "2026-06-<DD between 01-26>",
    "description": "2-3 sentence description specifically relevant to this candidate's background",
    "skills": ["candidate's skill 1", "skill 2", "skill 3", "skill 4", "skill 5"],
    "portal": "LinkedIn|Naukri|Indeed|Glassdoor|Instahyre",
    "portal_url": "<real search URL>",
    "url": "<real search URL for this company+role>",
    "job_type": "Full-time",
    "seniority": "Entry|Mid|Senior|Lead|Principal|Director",
    "match_score": <integer 78-97>
  }
]"""

FALLBACK_JOBS = [
    {
        "id": "job_fb001",
        "title": "Software Engineer",
        "company": "Google",
        "company_logo": "https://logo.clearbit.com/google.com",
        "location": "Bangalore, India",
        "remote": "Hybrid",
        "salary_min": 2500000,
        "salary_max": 4500000,
        "salary_currency": "INR",
        "experience_required": "3-6 years",
        "posted_date": "2024-01-15",
        "description": "Join Google's core infrastructure team. Work on distributed systems serving billions of users globally.",
        "skills": ["Python", "Go", "Kubernetes", "Distributed Systems", "SQL"],
        "portal": "LinkedIn",
        "portal_url": "https://linkedin.com/jobs",
        "job_type": "Full-time",
        "seniority": "Mid",
        "match_score": 80,
    },
    {
        "id": "job_fb002",
        "title": "Senior Engineer",
        "company": "Flipkart",
        "company_logo": "https://logo.clearbit.com/flipkart.com",
        "location": "Bangalore, India",
        "remote": "Hybrid",
        "salary_min": 3000000,
        "salary_max": 5000000,
        "salary_currency": "INR",
        "experience_required": "5-8 years",
        "posted_date": "2024-01-14",
        "description": "Scale Flipkart's platform to 500M+ customers. Own high-traffic services processing millions of transactions.",
        "skills": ["Java", "Kafka", "MySQL", "Redis", "Microservices"],
        "portal": "Naukri",
        "portal_url": "https://naukri.com",
        "job_type": "Full-time",
        "seniority": "Senior",
        "match_score": 75,
    },
]


async def generate_jobs_with_claude(
    query: str,
    location: str = "",
    experience_years: int = 0,
    remote: str = "",
    salary_min: int = 0,
) -> list[dict]:
    """Use Claude to generate realistic, relevant job listings for any search query."""
    context_parts = [f"Search query: {query}"]
    if location:
        context_parts.append(f"Location: {location} (use this city for most jobs)")
    if experience_years:
        context_parts.append(f"Experience: {experience_years} years")
    if remote:
        context_parts.append(f"Work type: {remote}")
    if salary_min:
        salary_l = salary_min // 100000
        context_parts.append(f"Minimum salary: ₹{salary_l}L per year")

    content = "\n".join(context_parts)
    content += "\n\nGenerate EXACTLY 8 realistic job listings. ALL must closely match the search query. Use the specified location and salary range."

    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_JOB_GENERATOR, messages, max_tokens=4096)
        raw = raw.strip()
        for pat in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
            m = re.search(pat, raw)
            if m:
                raw = m.group(1).strip()
                break
        # Find outermost array
        start = raw.find("[")
        if start != -1:
            end = raw.rfind("]")
            if end > start:
                raw = raw[start:end+1]
        jobs = json.loads(raw)
        if isinstance(jobs, list) and len(jobs) > 0:
            # Ensure IDs are unique
            for job in jobs:
                if not job.get("id") or job["id"] == "job_<unique_6char>":
                    job["id"] = f"job_{uuid.uuid4().hex[:6]}"
                # Ensure company_logo if missing
                if not job.get("company_logo") and job.get("company"):
                    domain = job["company"].lower().replace(" ", "") + ".com"
                    job["company_logo"] = f"https://logo.clearbit.com/{domain}"
            return jobs
    except Exception as e:
        logger.error(f"Claude job generation failed: {e}")
    return []


async def generate_jobs_with_resume(
    resume_profile: dict,
    location: str = "",
    experience_years: int = 0,
    remote: str = "",
    salary_min: int = 0,
) -> list[dict]:
    """Generate 8 highly targeted jobs based on the candidate's full resume profile."""
    personal = resume_profile.get("personal", {})
    name = personal.get("name", "Candidate")
    title = personal.get("title", "")
    resume_loc = location or personal.get("location", "Bangalore")

    skills_raw = resume_profile.get("skills", {})
    if isinstance(skills_raw, dict):
        all_skills = (
            skills_raw.get("technical", [])
            + skills_raw.get("soft", [])
            + skills_raw.get("tools", [])
        )
    elif isinstance(skills_raw, list):
        all_skills = skills_raw
    else:
        all_skills = []

    experience = resume_profile.get("experience", [])
    years_exp = experience_years
    if not years_exp and experience:
        years_exp = min(len(experience) * 2, 15)

    exp_lines = []
    for exp in experience[:3]:
        exp_lines.append(f"  - {exp.get('role', '')} at {exp.get('company', '')} ({exp.get('start', '')}–{'Present' if exp.get('current') else exp.get('end', '')})")

    summary = resume_profile.get("summary", "")

    content_parts = [
        f"Candidate: {name}",
        f"Current Title: {title}" if title else "",
        f"Location: {resume_loc}",
        f"Years of Experience: {years_exp}" if years_exp else "",
        f"Skills: {', '.join(all_skills[:15])}" if all_skills else "",
        "Recent Experience:\n" + "\n".join(exp_lines) if exp_lines else "",
        f"Summary: {summary[:300]}" if summary else "",
        f"Work Type Preference: {remote}" if remote else "",
        f"Minimum Salary: ₹{salary_min // 100000}L per annum" if salary_min else "",
    ]
    content = "\n".join([p for p in content_parts if p])
    content += "\n\nGenerate EXACTLY 8 perfectly matched jobs for this candidate. Every job must align tightly with their specific skills and background."

    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_RESUME_JOB_GENERATOR, messages, max_tokens=4096)
        raw = raw.strip()
        for pat in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
            m = re.search(pat, raw)
            if m:
                raw = m.group(1).strip()
                break
        start = raw.find("[")
        if start != -1:
            end = raw.rfind("]")
            if end > start:
                raw = raw[start : end + 1]
        jobs = json.loads(raw)
        if isinstance(jobs, list) and len(jobs) > 0:
            for job in jobs:
                if not job.get("id") or job["id"] == "job_<unique_6char>":
                    job["id"] = f"job_{uuid.uuid4().hex[:6]}"
                if not job.get("company_logo") and job.get("company"):
                    domain = job["company"].lower().replace(" ", "").replace(",", "") + ".com"
                    job["company_logo"] = f"https://logo.clearbit.com/{domain}"
            return jobs
    except Exception as e:
        logger.error(f"Resume-based job generation failed: {e}")
    return []


async def fetch_jobs_from_jsearch(
    query: str,
    location: str = "",
) -> list[dict]:
    """Fetch real job listings from JSearch API (RapidAPI). Returns [] on failure."""
    if not RAPIDAPI_KEY:
        return []

    search_query = f"{query} {location}".strip()
    url = "https://jsearch.p.rapidapi.com/search"
    params = {"query": search_query, "page": "1", "num_pages": "2", "date_posted": "all"}
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        raw_jobs = data.get("data", [])
        if not raw_jobs:
            return []

        jobs: list[dict] = []
        for rj in raw_jobs[:8]:
            job_title = rj.get("job_title", "")
            employer = rj.get("employer_name", "")
            city = rj.get("job_city") or rj.get("job_country") or "India"
            apply_link = rj.get("job_apply_link", "")
            description = rj.get("job_description", "")[:400]
            posted_dt = rj.get("job_posted_at_datetime_utc", "")
            emp_type = rj.get("job_employment_type") or "Full-time"
            required_skills = rj.get("job_required_skills") or []

            # Normalize salary to annual INR
            sal_min_raw = float(rj.get("job_min_salary") or 0)
            sal_max_raw = float(rj.get("job_max_salary") or 0)
            sal_period = (rj.get("job_salary_period") or "").upper()
            sal_currency = (rj.get("job_salary_currency") or "INR").upper()

            if sal_period in ("MONTH", "MONTHLY"):
                sal_min_raw *= 12
                sal_max_raw *= 12
            elif sal_period in ("HOUR", "HOURLY"):
                sal_min_raw *= 2080  # 40h/week × 52 weeks
                sal_max_raw *= 2080
            elif sal_period in ("WEEK", "WEEKLY"):
                sal_min_raw *= 52
                sal_max_raw *= 52

            if sal_currency in ("USD", "US$"):
                sal_min_raw *= 83
                sal_max_raw *= 83

            sal_min = int(sal_min_raw)
            sal_max = int(sal_max_raw)

            # Build portal info from apply_link domain
            portal = "JSearch"
            portal_url = apply_link
            if "linkedin.com" in apply_link:
                portal = "LinkedIn"
            elif "indeed.com" in apply_link:
                portal = "Indeed"
            elif "naukri.com" in apply_link:
                portal = "Naukri"
            elif "glassdoor.com" in apply_link:
                portal = "Glassdoor"

            domain = employer.lower().replace(" ", "").replace(",", "").replace(".", "") + ".com"

            jobs.append({
                "id": f"job_{uuid.uuid4().hex[:6]}",
                "title": job_title,
                "company": employer,
                "company_logo": f"https://logo.clearbit.com/{domain}",
                "location": f"{city}, India" if "india" not in city.lower() else city,
                "remote": "Remote" if rj.get("job_is_remote") else "On-site",
                "salary_min": sal_min,
                "salary_max": sal_max,
                "salary_currency": "INR",
                "experience_required": "",
                "posted_date": posted_dt[:10] if posted_dt else "",
                "description": description,
                "skills": required_skills[:6] if required_skills else [],
                "portal": portal,
                "portal_url": portal_url,
                "url": apply_link,
                "job_type": emp_type,
                "seniority": "Mid",
                "match_score": 0,  # Will be set by rank_jobs_for_profile
                "is_real_listing": True,
            })

        logger.info(f"JSearch returned {len(jobs)} real jobs for '{search_query}'")
        return jobs

    except Exception as e:
        logger.error(f"JSearch API failed: {e}")
        return []


async def search_jobs(
    query: str,
    location: str = "",
    experience_years: int = 0,
    salary_min: int = 0,
    job_type: str = "",
    remote: str = "",
    portals: list[str] = None,
) -> list[dict]:
    if not query or not query.strip():
        return FALLBACK_JOBS

    # Try JSearch (real listings) first
    if RAPIDAPI_KEY:
        real_jobs = await fetch_jobs_from_jsearch(query, location)
        if real_jobs:
            return real_jobs
        logger.warning("JSearch returned no results, falling back to Claude generation")

    # Fall back to Claude generation
    jobs = await generate_jobs_with_claude(query, location, experience_years, remote, salary_min)
    if jobs:
        # Mark as Claude-generated (not real listings) and set Google Jobs fallback URLs
        for job in jobs:
            job["is_real_listing"] = False
            if not job.get("url") or job["url"] in ("#", ""):
                title_enc = job.get("title", query).replace(" ", "+")
                company_enc = job.get("company", "").replace(" ", "+")
                job["url"] = f"https://www.google.com/search?q={title_enc}+{company_enc}+jobs"
        return jobs

    logger.warning(f"Claude generation failed for query: {query}, using fallback")
    # Fallback jobs also get Google search URLs
    for job in FALLBACK_JOBS:
        job["is_real_listing"] = False
        title_enc = job.get("title", "").replace(" ", "+")
        company_enc = job.get("company", "").replace(" ", "+")
        job.setdefault("url", f"https://www.google.com/search?q={title_enc}+{company_enc}+jobs")
    return FALLBACK_JOBS


async def rank_jobs_for_profile(jobs: list[dict], user_profile: dict) -> list[dict]:
    if not jobs:
        return jobs

    # Send only essential fields to keep token count low
    slim_jobs = [
        {
            "id": j.get("id"),
            "title": j.get("title"),
            "company": j.get("company"),
            "skills": j.get("skills", []),
            "experience_required": j.get("experience_required", ""),
            "seniority": j.get("seniority", ""),
            "description": (j.get("description") or "")[:150],
        }
        for j in jobs
    ]

    content = f"Candidate profile:\n{json.dumps(user_profile)}\n\nJob listings:\n{json.dumps(slim_jobs)}"
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_JOB_RANKER, messages, max_tokens=1500)
        raw = raw.strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end > start:
            raw = raw[start:end + 1]
        ranked_list = json.loads(raw)
        if isinstance(ranked_list, list):
            # Merge scores back into full job objects (preserves all original fields)
            score_map = {r.get("id"): r for r in ranked_list if isinstance(r, dict) and r.get("id")}
            for job in jobs:
                info = score_map.get(job.get("id"))
                if info:
                    job["match_score"] = info.get("match_score", job.get("match_score") or 70)
                    if info.get("why_match"):
                        job["why_match"] = info["why_match"]
                    if info.get("apply_priority"):
                        job["apply_priority"] = info["apply_priority"]
                elif not job.get("match_score"):
                    job["match_score"] = 60  # Default for unranked jobs
        return jobs
    except Exception as e:
        logger.error(f"Ranking failed: {e}")
        # Assign default differentiated scores if ranking fails
        for i, job in enumerate(jobs):
            if not job.get("match_score"):
                job["match_score"] = max(50, 80 - i * 5)
        return jobs


async def get_job_details(job_id: str) -> dict | None:
    return next((j for j in FALLBACK_JOBS if j["id"] == job_id), None)
