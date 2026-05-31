"""
Job Finder Agent — generates realistic, contextually relevant job listings via Claude.
"""
import json
import uuid
from services.claude_service import complete_claude_json
from loguru import logger

SYSTEM_JOB_GENERATOR = """You are India's most comprehensive real-time job database. Generate EXACTLY 8 realistic, highly relevant job listings for the given search query.

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
    "posted_date": "2024-01-<DD between 10-28>",
    "description": "2-3 sentence specific description with actual tech/product context",
    "skills": ["role-specific skill 1", "skill 2", "skill 3", "skill 4", "skill 5"],
    "portal": "LinkedIn|Naukri|Indeed|Glassdoor|Instahyre|AngelList",
    "portal_url": "https://linkedin.com/jobs",
    "job_type": "Full-time",
    "seniority": "Entry|Mid|Senior|Lead|Principal|Director",
    "match_score": <integer 72-95>
  }
]"""

SYSTEM_JOB_RANKER = """You are a career advisor. Given a user's profile and job listings, rank them by fit.
For each job, add:
- match_score: 0-100 based on skills/experience fit
- why_match: 2-sentence explanation
- red_flags: any concerns
- apply_priority: "high" | "medium" | "low"

Return the enriched jobs array as JSON."""

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
        # Extract JSON array robustly
        import re
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

    # Use Claude to generate contextually relevant jobs for the query
    jobs = await generate_jobs_with_claude(query, location, experience_years, remote, salary_min)
    if jobs:
        return jobs

    # Fallback: return default jobs with query appended to title
    logger.warning(f"Claude generation failed for query: {query}, using fallback")
    return FALLBACK_JOBS


async def rank_jobs_for_profile(jobs: list[dict], user_profile: dict) -> list[dict]:
    if not jobs:
        return jobs
    content = f"User profile: {json.dumps(user_profile)}\n\nJobs: {json.dumps(jobs)}"
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_JOB_RANKER, messages, max_tokens=2000)
        ranked = json.loads(raw)
        return ranked if isinstance(ranked, list) else jobs
    except Exception as e:
        logger.error(f"Ranking failed: {e}")
        return jobs


async def get_job_details(job_id: str) -> dict | None:
    return next((j for j in FALLBACK_JOBS if j["id"] == job_id), None)
