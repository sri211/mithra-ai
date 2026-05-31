"""
LinkedIn profile scraper — multi-strategy cascade with Playwright fallback.
Tries every possible method before giving up.
"""
import re
import json
import asyncio
import httpx
from loguru import logger
from bs4 import BeautifulSoup

# ─── Browser-like headers (Chrome 124 fingerprint) ───────────────────────────
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
}

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def extract_name_from_url(url: str) -> str:
    """Extract likely name from LinkedIn URL slug."""
    match = re.search(r"linkedin\.com/in/([^/?#]+)", url)
    if not match:
        return ""
    slug = match.group(1)
    slug = re.sub(r"-[a-z0-9]{5,8}$", "", slug)
    parts = slug.split("-")
    return " ".join(p.capitalize() for p in parts if p and not p.isdigit())


def parse_linkedin_html(html: str, url: str = "") -> str:
    """
    Exhaustively extract text from LinkedIn HTML using every technique:
    1. OG / meta tags (always present)
    2. JSON-LD structured data
    3. LinkedIn's embedded <code> tag JSON blobs
    4. Visible body text after cleaning
    """
    if not html or len(html) < 500:
        return ""

    parts = []
    name_from_url = extract_name_from_url(url) if url else ""
    if name_from_url:
        parts.append(f"Name (from URL): {name_from_url}")

    # ── 1. OG / meta tags ────────────────────────────────────────────────────
    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
    og_desc  = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html)
    og_alt   = re.search(r'<meta[^>]+property=["\']og:image:alt["\'][^>]+content=["\']([^"\']+)["\']', html)
    # also handle reversed attribute order
    if not og_title:
        og_title = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html)
    if not og_desc:
        og_desc = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', html)

    if og_title:
        parts.append(f"Name/Headline: {og_title.group(1)}")
    if og_desc:
        parts.append(f"Summary/About: {og_desc.group(1)}")
    if og_alt:
        parts.append(f"Profile alt: {og_alt.group(1)}")

    # ── 2. JSON-LD structured data ───────────────────────────────────────────
    jsonld_blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    for block in jsonld_blocks[:5]:
        block = block.strip()
        try:
            data = json.loads(block)
            extracted = _extract_from_jsonld(data)
            if extracted:
                parts.extend(extracted)
        except Exception:
            pass

    # ── 3. LinkedIn's embedded <code> tag JSON blobs ─────────────────────────
    # LinkedIn stores profile data in <code id="bpr-guid-..."> tags as JSON
    code_blocks = re.findall(r'<code[^>]*>(.*?)</code>', html, re.DOTALL)
    for block in code_blocks[:30]:
        block = block.strip()
        if len(block) < 20:
            continue
        # Look for profile-like JSON
        if any(kw in block for kw in ['"firstName"', '"lastName"', '"headline"', '"summary"', '"positions"', '"educations"', '"skills"', '"certifications"']):
            try:
                data = json.loads(block)
                extracted = _extract_from_profile_json(data)
                if extracted:
                    parts.extend(extracted)
            except Exception:
                # Try to extract sub-objects
                sub_jsons = re.findall(r'\{[^{}]*(?:"firstName"|"lastName"|"headline"|"summary")[^{}]*\}', block)
                for sj in sub_jsons[:5]:
                    try:
                        d = json.loads(sj)
                        if d.get("firstName") or d.get("headline"):
                            parts.append(f"Profile data: {json.dumps(d)[:300]}")
                    except Exception:
                        pass

    # ── 4. Visible body text via BeautifulSoup ───────────────────────────────
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "meta", "link", "nav", "footer", "header"]):
            tag.decompose()

        # Try to find profile sections
        sections = []
        for selector in [
            "section.core-section-container",
            "section[data-section]",
            ".profile-section-card",
            ".experience-section",
            ".education-section",
            ".skills-section",
            "main",
            "article",
            ".profile__body-container",
            "#profile-content",
        ]:
            found = soup.select(selector)
            if found:
                for el in found[:5]:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        sections.append(text[:2000])

        if sections:
            parts.append("Profile sections:\n" + "\n---\n".join(sections[:6]))
        else:
            # Fallback: full body text
            body = soup.find("body")
            if body:
                body_text = body.get_text(separator="\n", strip=True)
                # Filter out navigation noise
                lines = [l.strip() for l in body_text.splitlines() if l.strip() and len(l.strip()) > 15]
                meaningful = [l for l in lines if not l.lower().startswith(("sign in", "join linkedin", "agree", "cookie", "privacy"))]
                if meaningful:
                    parts.append("Profile text:\n" + "\n".join(meaningful[:100]))
    except Exception as e:
        logger.warning(f"BeautifulSoup parsing failed: {e}")

    result = "\n\n".join(parts)
    return result if len(result) > 50 else ""


def _extract_from_jsonld(data: dict) -> list:
    """Extract from JSON-LD structured data."""
    parts = []
    if isinstance(data, list):
        for item in data:
            parts.extend(_extract_from_jsonld(item))
        return parts
    if not isinstance(data, dict):
        return parts

    name = data.get("name") or f"{data.get('givenName', '')} {data.get('familyName', '')}".strip()
    if name:
        parts.append(f"Name: {name}")
    if data.get("jobTitle"):
        parts.append(f"Title: {data['jobTitle']}")
    if data.get("description"):
        parts.append(f"About: {data['description']}")
    if data.get("url"):
        parts.append(f"LinkedIn: {data['url']}")
    if data.get("sameAs"):
        parts.append(f"Links: {data['sameAs']}")
    if data.get("worksFor"):
        wf = data["worksFor"]
        if isinstance(wf, dict):
            parts.append(f"Works at: {wf.get('name', '')}")
    return parts


def _extract_from_profile_json(data: object, depth: int = 0) -> list:
    """Recursively extract profile data from LinkedIn's internal JSON."""
    if depth > 5 or not data:
        return []
    parts = []

    if isinstance(data, list):
        for item in data[:10]:
            parts.extend(_extract_from_profile_json(item, depth + 1))
        return parts

    if not isinstance(data, dict):
        return parts

    # Direct fields
    first = data.get("firstName", {})
    last = data.get("lastName", {})
    if isinstance(first, dict):
        first = first.get("localized", {})
        first = list(first.values())[0] if first else ""
    if isinstance(last, dict):
        last = last.get("localized", {})
        last = list(last.values())[0] if last else ""
    if first or last:
        parts.append(f"Name: {first} {last}".strip())

    if data.get("headline"):
        headline = data["headline"]
        if isinstance(headline, dict):
            headline = list(headline.get("localized", {}).values() or [headline.get("text", "")])[0]
        parts.append(f"Headline: {headline}")

    if data.get("summary"):
        summary = data["summary"]
        if isinstance(summary, dict):
            summary = list(summary.get("localized", {}).values() or [""])[0]
        parts.append(f"Summary: {summary}")

    if data.get("locationName"):
        parts.append(f"Location: {data['locationName']}")

    # Positions / experience
    positions = data.get("positions", data.get("elements", []))
    if isinstance(positions, dict):
        positions = positions.get("elements", [])
    if isinstance(positions, list) and positions:
        exp_parts = []
        for pos in positions[:10]:
            if not isinstance(pos, dict):
                continue
            title = pos.get("title") or pos.get("localizedTitle") or ""
            company = pos.get("companyName") or (pos.get("company", {}) or {}).get("name") or ""
            start = pos.get("startMonthYear") or pos.get("start") or {}
            end = pos.get("endMonthYear") or pos.get("end") or {}
            desc = pos.get("description") or ""
            if isinstance(start, dict):
                start = f"{start.get('month', '')}/{start.get('year', '')}".strip("/")
            if isinstance(end, dict):
                end = f"{end.get('month', '')}/{end.get('year', '')}".strip("/") or "Present"
            if title or company:
                exp_parts.append(f"Role: {title} at {company} ({start}-{end})\n{desc}")
        if exp_parts:
            parts.append("Experience:\n" + "\n\n".join(exp_parts))

    # Education
    educations = data.get("educations", data.get("education", []))
    if isinstance(educations, dict):
        educations = educations.get("elements", [])
    if isinstance(educations, list) and educations:
        edu_parts = []
        for edu in educations[:5]:
            if not isinstance(edu, dict):
                continue
            school = edu.get("schoolName") or (edu.get("school", {}) or {}).get("name") or ""
            degree = edu.get("degreeName") or edu.get("degree") or ""
            field = edu.get("fieldOfStudy") or ""
            start_y = (edu.get("startMonthYear") or {}).get("year") or edu.get("startYear") or ""
            end_y = (edu.get("endMonthYear") or {}).get("year") or edu.get("endYear") or ""
            if school or degree:
                edu_parts.append(f"{degree} {field} - {school} ({start_y}-{end_y})")
        if edu_parts:
            parts.append("Education:\n" + "\n".join(edu_parts))

    # Skills
    skills = data.get("skills", data.get("skillsV2", []))
    if isinstance(skills, dict):
        skills = skills.get("elements", [])
    if isinstance(skills, list) and skills:
        skill_names = []
        for s in skills[:30]:
            if isinstance(s, dict):
                name = s.get("name") or s.get("localizedName") or (s.get("skill") or {}).get("name") or ""
                if name:
                    skill_names.append(name)
        if skill_names:
            parts.append(f"Skills: {', '.join(skill_names)}")

    # Certifications
    certs = data.get("certifications", [])
    if isinstance(certs, dict):
        certs = certs.get("elements", [])
    if isinstance(certs, list) and certs:
        cert_parts = []
        for c in certs[:10]:
            if isinstance(c, dict):
                name = c.get("name") or c.get("localizedName") or ""
                authority = c.get("authority") or (c.get("company") or {}).get("name") or ""
                if name:
                    cert_parts.append(f"{name} ({authority})")
        if cert_parts:
            parts.append(f"Certifications: {', '.join(cert_parts)}")

    # Recurse into sub-objects
    for key in ["profile", "miniProfile", "data", "included", "elements"]:
        if key in data and isinstance(data[key], (dict, list)):
            parts.extend(_extract_from_profile_json(data[key], depth + 1))

    return parts


# ─── Strategy 1: Direct fetch with Chrome fingerprint ────────────────────────
async def try_direct_fetch(url: str) -> str:
    """Direct HTTP fetch with realistic browser headers."""
    for headers in [BROWSER_HEADERS, MOBILE_HEADERS]:
        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=12,
                http2=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    result = parse_linkedin_html(resp.text, url)
                    if result and len(result) > 100:
                        logger.info(f"Direct fetch succeeded for {url}")
                        return result
        except Exception as e:
            logger.debug(f"Direct fetch attempt failed: {e}")
    return ""


# ─── Strategy 2: Playwright (headless Chrome) ────────────────────────────────
async def try_playwright_fetch(url: str) -> str:
    """Use Playwright to render LinkedIn page before login redirect kicks in."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="Asia/Kolkata",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                },
            )
            # Remove automation markers
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)
            page = await context.new_page()

            # Navigate and capture content before/during page load
            html_snapshots = []

            async def capture_response(response):
                if "linkedin.com/in/" in response.url and response.status == 200:
                    try:
                        body = await response.body()
                        html_snapshots.append(body.decode("utf-8", errors="ignore"))
                    except Exception:
                        pass

            page.on("response", capture_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)  # Wait for JS to render

                # Capture current page content
                html = await page.content()
                html_snapshots.append(html)

                # Try to scroll to load more content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await page.wait_for_timeout(1500)
                html = await page.content()
                html_snapshots.append(html)

            except Exception as e:
                logger.debug(f"Playwright navigation error: {e}")

            await browser.close()

            # Try parsing each snapshot (most complete first)
            for snap in reversed(html_snapshots):
                if len(snap) > 1000:
                    result = parse_linkedin_html(snap, url)
                    if result and len(result) > 150:
                        logger.info(f"Playwright fetch succeeded: extracted {len(result)} chars")
                        return result

    except ImportError:
        logger.warning("Playwright not installed")
    except Exception as e:
        logger.warning(f"Playwright fetch failed: {e}")
    return ""


# ─── Strategy 3: Wayback Machine (Archive.org) ───────────────────────────────
async def try_wayback_machine(url: str) -> str:
    """Fetch from Archive.org's Wayback Machine — may have cached profile."""
    try:
        # Get latest available snapshot
        cdx_url = f"https://archive.org/wayback/available?url={url}"
        async with httpx.AsyncClient(timeout=10) as client:
            cdx_resp = await client.get(cdx_url)
            if cdx_resp.status_code == 200:
                cdx_data = cdx_resp.json()
                snapshot = cdx_data.get("archived_snapshots", {}).get("closest", {})
                if snapshot.get("available") and snapshot.get("url"):
                    snapshot_url = snapshot["url"]
                    logger.info(f"Wayback Machine snapshot found: {snapshot_url}")
                    resp = await client.get(snapshot_url, follow_redirects=True, timeout=15)
                    if resp.status_code == 200 and len(resp.text) > 1000:
                        result = parse_linkedin_html(resp.text, url)
                        if result and len(result) > 100:
                            logger.info("Wayback Machine fetch succeeded")
                            return f"[Archived profile - may not be current]\n{result}"
    except Exception as e:
        logger.debug(f"Wayback Machine failed: {e}")
    return ""


# ─── Strategy 4: Bing Cache ──────────────────────────────────────────────────
async def try_bing_cache(url: str) -> str:
    """Try Bing's cached version of the LinkedIn page."""
    try:
        # Bing cache URL format
        bing_url = f"https://cc.bingj.com/cache.aspx?q=linkedin+profile&url={url}&d=1"
        async with httpx.AsyncClient(
            headers={"User-Agent": BROWSER_HEADERS["User-Agent"]},
            follow_redirects=True, timeout=12,
        ) as client:
            resp = await client.get(bing_url)
            if resp.status_code == 200 and len(resp.text) > 2000:
                result = parse_linkedin_html(resp.text, url)
                if result and len(result) > 100:
                    logger.info("Bing cache fetch succeeded")
                    return f"[Bing cached version]\n{result}"
    except Exception as e:
        logger.debug(f"Bing cache failed: {e}")
    return ""


# ─── Strategy 5: Google AMP cache ───────────────────────────────────────────
async def try_google_amp(url: str) -> str:
    """Try Google's AMP CDN cache for LinkedIn."""
    try:
        # Convert linkedin.com URL to AMP cache format
        match = re.search(r"linkedin\.com/in/([^/?#]+)", url)
        if not match:
            return ""
        username = match.group(1)
        # Try Google cache directly with a search snippet approach
        search_url = f"https://www.google.com/search?q=site:linkedin.com/in/{username}&num=1"
        async with httpx.AsyncClient(
            headers={
                "User-Agent": BROWSER_HEADERS["User-Agent"],
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True, timeout=10,
        ) as client:
            resp = await client.get(search_url)
            if resp.status_code == 200:
                # Extract snippets from Google search results
                soup = BeautifulSoup(resp.text, "lxml")
                snippets = []
                for el in soup.select(".VwiC3b, .s, .st, .IsZvec, .aCOpRe"):
                    text = el.get_text(strip=True)
                    if len(text) > 30:
                        snippets.append(text)
                if snippets:
                    name = extract_name_from_url(url)
                    result = f"Name: {name}\nProfile snippets from search:\n" + "\n".join(snippets[:5])
                    logger.info("Google search snippets extracted")
                    return result
    except Exception as e:
        logger.debug(f"Google AMP failed: {e}")
    return ""


# ─── Strategy 6: LinkedIn JSON API (unauthenticated) ────────────────────────
async def try_linkedin_json_api(url: str) -> str:
    """
    LinkedIn serves some data via its Voyager API even for unauthenticated users.
    Try to extract the embedded Apollo state / Ember data from the page.
    """
    try:
        match = re.search(r"linkedin\.com/in/([^/?#]+)", url)
        if not match:
            return ""
        username = match.group(1)
        # LinkedIn serves initial data as window.__initialData__ or in script tags
        api_headers = {
            "User-Agent": BROWSER_HEADERS["User-Agent"],
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "x-li-track": '{"clientVersion":"1.13.3"}',
        }
        async with httpx.AsyncClient(headers=api_headers, timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                # Look for embedded data in script tags
                script_data = re.findall(
                    r'window\.__INITIAL_STATE__\s*=\s*({.*?})(?:;|\n)',
                    resp.text, re.DOTALL
                )
                for block in script_data[:3]:
                    try:
                        data = json.loads(block[:50000])
                        extracted = _extract_from_profile_json(data)
                        if extracted and len("\n".join(extracted)) > 100:
                            return "\n".join(extracted)
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"LinkedIn JSON API failed: {e}")
    return ""


# ─── Main entry point ────────────────────────────────────────────────────────
async def fetch_profile_text(url: str) -> str:
    """
    Try every possible strategy to fetch LinkedIn profile data.
    Returns extracted text (may be partial) or empty string.
    """
    logger.info(f"Fetching LinkedIn profile: {url}")

    # Run strategies in order — return first success with meaningful content
    strategies = [
        ("Playwright (headless Chrome)", try_playwright_fetch),
        ("Direct HTTP fetch", try_direct_fetch),
        ("LinkedIn JSON API", try_linkedin_json_api),
        ("Bing cache", try_bing_cache),
        ("Google search snippets", try_google_amp),
        ("Wayback Machine", try_wayback_machine),
    ]

    for name, strategy in strategies:
        logger.info(f"Trying strategy: {name}")
        try:
            result = await strategy(url)
            if result and len(result.strip()) > 100:
                logger.info(f"SUCCESS with strategy: {name} ({len(result)} chars)")
                return result
        except Exception as e:
            logger.warning(f"Strategy '{name}' threw exception: {e}")

    # All strategies failed — return just the name from URL
    name = extract_name_from_url(url)
    if name:
        logger.warning(f"All strategies failed for {url}, returning name-only skeleton")
        return f"LinkedIn Profile URL: {url}\nName (from URL slug): {name}\n[All scraping strategies failed — LinkedIn is blocking. Please paste your profile text instead.]"
    return ""


async def enrich_linkedin_input(raw_input: str) -> str:
    """
    Given raw input (URL or pasted text), return the richest possible
    text for Claude to build a resume from.
    """
    url_match = re.search(r"https?://[^\s]*linkedin\.com/in/[^\s]+", raw_input)

    if url_match:
        url = url_match.group(0).rstrip(".,)>\"'")
        name = extract_name_from_url(url)

        # Try to fetch full profile data
        fetched = await fetch_profile_text(url)

        enriched = f"LinkedIn Profile URL: {url}\n"
        if name:
            enriched += f"Name (from URL): {name}\n"
        if fetched and len(fetched) > 100:
            enriched += f"\n=== FETCHED PROFILE DATA ===\n{fetched}\n=== END FETCHED DATA ===\n"
        enriched += f"\nOriginal input: {raw_input}"
        return enriched

    # Already has pasted content — return as-is
    return raw_input
