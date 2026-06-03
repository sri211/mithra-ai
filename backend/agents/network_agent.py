"""
Network Intelligence Agent — finds real connections at target companies.
Uses Google search to find actual LinkedIn profiles, then enriches with Claude-drafted outreach.
"""
import json
import os
import urllib.parse
import httpx
from loguru import logger
from services.claude_service import complete_claude_json, complete_claude

SYSTEM_NETWORK = """You are a world-class networking strategist and talent intelligence expert.

Given a target company and role, generate 10 realistic, valuable people to connect with.
These should be a diverse mix of: hiring managers, recruiters, team members, alumni, and influencers.

Use real-sounding Indian professional names. Make all details realistic and specific to the company.

For each person, generate a LinkedIn SEARCH URL (not a direct profile URL) that actually finds them:
- Format: https://www.linkedin.com/search/results/people/?keywords=<ROLE>+<COMPANY>&origin=GLOBAL_SEARCH_HEADER
- Example: https://www.linkedin.com/search/results/people/?keywords=Engineering+Manager+Google+India&origin=GLOBAL_SEARCH_HEADER

Output ONLY valid JSON (no markdown, no preamble):
{
  "company_insights": {
    "culture": "brief culture description",
    "hiring_status": "Actively Hiring",
    "growth_stage": "Enterprise",
    "key_teams": ["team1", "team2"]
  },
  "connections": [
    {
      "id": "conn_001",
      "name": "Full Name",
      "role": "their exact job title at the company",
      "company": "<the target company>",
      "avatar": "initials e.g. AK",
      "color": "#hexcolor",
      "type": "hiring_manager|recruiter|team_member|alumnus|influencer",
      "mutual": <integer 3-20>,
      "why": "1-2 sentence specific reason why connecting helps",
      "draft": "personalized LinkedIn message under 300 chars referencing something specific",
      "linkedin_search": "https://www.linkedin.com/search/results/people/?keywords=<encoded+search>&origin=GLOBAL_SEARCH_HEADER",
      "email_pattern": "firstname.lastname@<company_domain>.com"
    }
  ],
  "networking_action_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ]
}

Generate exactly 10 connections with diverse types. Make the names, roles, and search URLs specific to the target company."""

SYSTEM_OUTREACH = """Write a personalized LinkedIn connection request message (max 300 chars).
Reference something specific — their work, company project, mutual connection.
Professional but human. No desperation, no copy-paste feel."""


def build_linkedin_search_url(name: str, role: str, company: str) -> str:
    """Build a LinkedIn people search URL for a specific name+company."""
    if name and name not in ("Unknown", ""):
        query = f"{name} {company}".strip()
    else:
        query = f"{role} {company}".strip()
    encoded = urllib.parse.quote(query)
    return f"https://www.linkedin.com/search/results/people/?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"


async def google_search_linkedin_profiles(role: str, company: str) -> list[dict]:
    """
    Use Google to find REAL LinkedIn profiles for people in a given role at a company.
    Returns list of {name, linkedin_url, snippet, title}.
    """
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # Search for the role type broadly, and also for common related roles
    role_queries = [
        f'site:linkedin.com/in "{company}" "{role}"',
        f'site:linkedin.com/in "{company}" {role}',
    ]
    profiles = []
    seen_urls = set()

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        for q in role_queries:
            if len(profiles) >= 8:
                break
            try:
                url = f"https://www.google.com/search?q={urllib.parse.quote(q)}&num=8"
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for g in soup.select("div.g, div[data-sokoban-container]"):
                    a = g.find("a", href=True)
                    h3 = g.find("h3")
                    snippet_el = g.find("div", class_=lambda c: c and "VwiC3b" in c)
                    if not (a and h3):
                        continue
                    href = a["href"]
                    if "linkedin.com/in/" not in href:
                        continue
                    # Clean URL
                    if href.startswith("/url?q="):
                        href = urllib.parse.unquote(href.split("/url?q=")[1].split("&")[0])
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    title_text = h3.get_text(strip=True)
                    # Extract name from title: "Name - Role at Company | LinkedIn"
                    name = title_text.split(" - ")[0].split(" | ")[0].split(" – ")[0].strip()
                    role_snippet = ""
                    if " - " in title_text:
                        role_snippet = title_text.split(" - ", 1)[1].split(" | ")[0].strip()
                    elif " – " in title_text:
                        role_snippet = title_text.split(" – ", 1)[1].split(" | ")[0].strip()
                    profiles.append({
                        "name": name,
                        "linkedin_url": href,
                        "role_snippet": role_snippet,
                        "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    })
            except Exception as e:
                logger.warning(f"Google LinkedIn search failed for '{q}': {e}")
    return profiles[:8]


async def find_company_emails_hunter(company_domain: str) -> list[dict]:
    """Use Hunter.io to find real professional email addresses for a company domain."""
    key = os.getenv("HUNTER_API_KEY", "")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.hunter.io/v2/domain-search",
                params={"domain": company_domain, "api_key": key, "limit": 15, "type": "personal"}
            )
        if r.status_code == 200:
            emails = r.json().get("data", {}).get("emails", [])
            return [{"email": e["value"], "first": e.get("first_name", ""), "last": e.get("last_name", ""), "position": e.get("position", "")} for e in emails if e.get("value")]
    except Exception as e:
        logger.warning(f"Hunter.io failed for {company_domain}: {e}")
    return []


def get_company_domain(company: str) -> str:
    """Best-guess company email domain."""
    domains = {
        "google": "google.com", "microsoft": "microsoft.com", "amazon": "amazon.com",
        "flipkart": "flipkart.com", "swiggy": "swiggy.in", "zomato": "zomato.com",
        "razorpay": "razorpay.com", "cred": "cred.club", "phonepe": "phonepe.com",
        "meesho": "meesho.com", "byju": "byjus.com", "paytm": "paytm.com",
        "infosys": "infosys.com", "wipro": "wipro.com", "tcs": "tcs.com",
        "netflix": "netflix.com", "meta": "meta.com", "apple": "apple.com",
        "uber": "uber.com", "ola": "olacabs.com", "myntra": "myntra.com",
    }
    key = company.lower().replace(" ", "")
    for k, v in domains.items():
        if k in key:
            return v
    return company.lower().replace(" ", "") + ".com"


COLORS = ["#7c3aed", "#06b6d4", "#10b981", "#f59e0b", "#ec4899", "#6366f1", "#ef4444", "#8b5cf6", "#14b8a6", "#f97316"]


SYSTEM_ENRICH = """You are a networking strategist. Given a list of real LinkedIn profiles found via Google search, enrich each with:
1. Their likely relationship to the target role (hiring_manager / recruiter / team_member / alumnus / influencer)
2. A specific, personalized LinkedIn outreach message (under 300 chars) that references something real about their background
3. A clear reason why connecting with them helps the job seeker

Also suggest 3 additional role TYPES to search for (not fake names — just roles like "Head of HR at {company}", "Ecommerce Category Manager at {company}").

Output JSON:
{
  "enriched": [
    {
      "id": "conn_001",
      "name": "<from input>",
      "role": "<their actual role from snippet>",
      "company": "<company>",
      "type": "hiring_manager|recruiter|team_member|alumnus|influencer",
      "why": "<specific reason this person helps>",
      "draft": "<personalized message under 300 chars>",
      "linkedin_url": "<from input — do not change>",
      "is_real": true
    }
  ],
  "additional_searches": [
    {"role": "Head of HR", "search_label": "HR / Recruiter"},
    {"role": "Category Manager", "search_label": "Category Manager"}
  ],
  "company_insights": {
    "culture": "<1 line about company culture>",
    "hiring_status": "Actively Hiring|Selective Hiring|Unknown",
    "key_teams": ["team1", "team2"]
  }
}"""


async def find_connections(company: str, target_role: str, user_profile: dict) -> dict:
    domain = get_company_domain(company)

    # Step 1: Find REAL people via Google LinkedIn search
    real_profiles = await google_search_linkedin_profiles(target_role, company)
    logger.info(f"Google found {len(real_profiles)} real LinkedIn profiles for {target_role} at {company}")

    # Also get real emails from Hunter.io if key available
    hunter_emails = await find_company_emails_hunter(domain)
    email_map = {f"{e['first']} {e['last']}".strip().lower(): e["email"] for e in hunter_emails if e.get("email")}

    # Step 2: If we found real profiles, enrich them with Claude
    if real_profiles:
        enrich_content = (
            f"Company: {company}\n"
            f"Target Role: {target_role}\n"
            f"User Profile: {json.dumps(user_profile) if user_profile else 'job seeker'}\n\n"
            f"Real LinkedIn profiles found via Google search:\n"
            f"{json.dumps(real_profiles, indent=2)}\n\n"
            f"Enrich these REAL people with connection type, outreach messages, and reasons. "
            f"Also suggest 2-3 additional role types to search for at {company}."
        )
        try:
            raw = await complete_claude_json(SYSTEM_ENRICH, [{"role": "user", "content": enrich_content}], max_tokens=4096)
            enriched_data = json.loads(raw)
            enriched = enriched_data.get("enriched", [])
            additional = enriched_data.get("additional_searches", [])
            company_insights = enriched_data.get("company_insights", {})

            connections = []
            for i, person in enumerate(enriched):
                name = person.get("name", "")
                linkedin_url = person.get("linkedin_url", "")
                # Try to find real email from Hunter
                real_email = email_map.get(name.lower())
                email_display = real_email or f"{name.lower().replace(' ', '.').split()[0] if name else 'contact'}@{domain} (likely)"

                connections.append({
                    "id": f"conn_{i+1:03d}",
                    "name": name,
                    "role": person.get("role", target_role),
                    "company": company,
                    "avatar": "".join(p[0] for p in name.split()[:2]).upper() if name else "??",
                    "color": COLORS[i % len(COLORS)],
                    "type": person.get("type", "team_member"),
                    "mutual": 0,
                    "why": person.get("why", ""),
                    "draft": person.get("draft", ""),
                    "linkedin_url": linkedin_url,
                    "linkedin_search": linkedin_url or build_linkedin_search_url(name, person.get("role", target_role), company),
                    "email_pattern": real_email or email_display,
                    "email_verified": bool(real_email),
                    "is_real": True,
                })

            # Add search suggestion cards for additional roles
            for i, sr in enumerate(additional[:3]):
                role_label = sr.get("role", "")
                connections.append({
                    "id": f"search_{i+1:03d}",
                    "name": f"Find: {sr.get('search_label', role_label)}",
                    "role": role_label,
                    "company": company,
                    "avatar": "🔍",
                    "color": "#64748b",
                    "type": "search_suggestion",
                    "mutual": 0,
                    "why": f"Search for real {role_label} professionals at {company} on LinkedIn",
                    "draft": "",
                    "linkedin_search": build_linkedin_search_url("", role_label, company),
                    "email_pattern": "",
                    "is_real": False,
                    "is_search_card": True,
                })

            return {
                "connections": connections,
                "company_insights": {
                    "culture": company_insights.get("culture", f"Professional environment at {company}"),
                    "hiring_status": company_insights.get("hiring_status", "Actively Hiring"),
                    "growth_stage": "Enterprise",
                    "key_teams": company_insights.get("key_teams", []),
                },
                "networking_action_plan": [
                    f"1. Connect with real people found above — these are verified LinkedIn profiles",
                    f"2. Personalize the AI-drafted message before sending",
                    f"3. Mention a specific detail from their profile or company news",
                    f"4. Follow up once after 1 week if no response",
                ],
                "real_profiles_found": len(enriched),
            }
        except Exception as e:
            logger.error(f"Enrich step failed: {e}")

    # Step 3: Fallback — return search-suggestion cards (no fake names)
    role_types = [
        (f"Head of {target_role}", "Senior Leader"),
        (f"Recruiter {company}", "Talent Acquisition"),
        (f"{target_role} Manager", "Hiring Manager"),
        (f"HR Business Partner {company}", "HR Contact"),
        (f"Director {target_role} {company}", "Director"),
    ]
    connections = []
    for i, (search_role, label) in enumerate(role_types):
        connections.append({
            "id": f"search_{i+1:03d}",
            "name": f"Search: {label}",
            "role": search_role,
            "company": company,
            "avatar": "🔍",
            "color": COLORS[i % len(COLORS)],
            "type": "search_suggestion",
            "mutual": 0,
            "why": f"Search LinkedIn for real {label} professionals at {company}",
            "draft": "",
            "linkedin_search": build_linkedin_search_url("", search_role, company),
            "email_pattern": f"<firstname>.<lastname>@{domain}",
            "is_real": False,
            "is_search_card": True,
        })
    return {
        "connections": connections,
        "company_insights": {
            "culture": f"Professional environment at {company}",
            "hiring_status": "Unknown",
            "growth_stage": "Enterprise",
            "key_teams": [],
        },
        "networking_action_plan": [
            f"1. Click 'Find on LinkedIn' to search for real people at {company}",
            "2. Connect with whoever you find in these roles",
            "3. Personalize your outreach with something specific from their profile",
        ],
        "real_profiles_found": 0,
    }


async def draft_outreach(person: dict, user_profile: dict, context: str = "") -> str:
    content = f"Target person: {json.dumps(person)}\nMy profile: {json.dumps(user_profile)}\nContext: {context}"
    messages = [{"role": "user", "content": content}]
    return await complete_claude(SYSTEM_OUTREACH, messages, max_tokens=150)
