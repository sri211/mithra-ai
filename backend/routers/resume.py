from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents.resume_builder_agent import build_from_qa, build_from_linkedin, enhance_bullet, stream_build, edit_resume_with_instruction
from agents.resume_adaptor_agent import adapt_resume, parse_job_description, generate_cover_letter, score_resume_vs_jd
from services.linkedin_scraper import enrich_linkedin_input
import json
import io
import httpx
from bs4 import BeautifulSoup

router = APIRouter()

# System prompt for structured extraction
SYSTEM_EXTRACT = """You are a resume data extraction specialist. Your ONLY job is to read an existing resume document and extract the data into structured JSON.

CRITICAL RULES:
1. EXTRACT ONLY — never invent, infer, or add information not explicitly written in the document
2. Preserve the EXACT job titles, company names, and dates as written
3. Preserve the EXACT industry and domain — never change it
4. Copy bullet points verbatim or very close to verbatim
5. If a field is not present, leave it as empty string or empty array
6. The "title" field = their most recent or primary job title exactly as written
7. For LinkedIn PDF exports: the format has sections like "Experience", "Education", "Skills" — extract all of them

Output ONLY valid JSON:
{
  "personal": {"name":"","email":"","phone":"","location":"","linkedin":"","github":"","website":"","title":""},
  "summary": "",
  "experience": [{"company":"","role":"","start":"","end":"","location":"","current":false,"bullets":[]}],
  "education": [{"institution":"","degree":"","field":"","start":"","end":"","gpa":""}],
  "skills": {"technical":[],"soft":[],"languages":[],"certifications":[]},
  "projects": [{"name":"","description":"","tech":[],"link":"","bullets":[]}],
  "achievements": [],
  "volunteer": []
}"""


class LinkedInRequest(BaseModel):
    linkedin_text: str


class QABuildRequest(BaseModel):
    conversation: list[dict]


class BulletRequest(BaseModel):
    bullet: str
    role: str
    company: str


class AdaptRequest(BaseModel):
    resume: dict
    jd_text: str


class CoverLetterRequest(BaseModel):
    resume: dict
    jd_text: str
    tone: str = "professional"


class ScoreRequest(BaseModel):
    resume: dict
    jd_text: str


class FetchJDRequest(BaseModel):
    url: str

class ParseFileRequest(BaseModel):
    file_text: str
    file_name: str = ""


@router.post("/build/linkedin")
async def build_from_linkedin_route(req: LinkedInRequest):
    try:
        # Enrich URL with scraped public data + name extraction before sending to Claude
        enriched = await enrich_linkedin_input(req.linkedin_text)
        resume = await build_from_linkedin(enriched)
        return {"resume": resume}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/build/qa")
async def build_from_qa_route(req: QABuildRequest):
    try:
        resume = await build_from_qa(req.conversation)
        return {"resume": resume}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enhance-bullet")
async def enhance_bullet_route(req: BulletRequest):
    try:
        enhanced = await enhance_bullet(req.bullet, req.role, req.company)
        return {"enhanced": enhanced}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapt")
async def adapt_resume_route(req: AdaptRequest):
    try:
        jd_parsed = await parse_job_description(req.jd_text)
        result = await adapt_resume(req.resume, req.jd_text, jd_parsed)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cover-letter")
async def cover_letter_route(req: CoverLetterRequest):
    try:
        letter = await generate_cover_letter(req.resume, req.jd_text, req.tone)
        return {"cover_letter": letter}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score")
async def score_route(req: ScoreRequest):
    try:
        score = await score_resume_vs_jd(req.resume, req.jd_text)
        return score
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-file")
async def parse_file_route(req: ParseFileRequest):
    """Parse resume text — used when text is pre-extracted client-side."""
    from services.claude_service import complete_claude_json
    import json as _json

    prompt = f"""File: {req.file_name}

Document content:
{req.file_text[:12000]}

Extract every field exactly as written. Do not change job titles, industries, or add information."""

    try:
        raw = await complete_claude_json(SYSTEM_EXTRACT, [{"role": "user", "content": prompt}], max_tokens=4096)
        resume = _json.loads(raw)
        return {"resume": resume}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload")
async def upload_resume_file(file: UploadFile = File(...)):
    """
    Upload a PDF, DOCX, or TXT file as binary — properly extracts text using
    PyMuPDF (for PDF) or python-docx (for DOCX) before passing to Claude.
    This is the CORRECT way to handle PDF uploads, not readAsText().
    """
    from services.claude_service import complete_claude_json as ccj
    import json as _json

    filename = file.filename or "resume"
    ext = filename.lower().rsplit(".", 1)[-1]
    content = await file.read()

    text = ""

    if ext == "pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=content, filetype="pdf")
            for page_num in range(len(doc)):
                page = doc[page_num]
                text += page.get_text("text") + "\n\n"
            doc.close()
            text = text.strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read PDF: {str(e)}. Make sure the PDF is not a scanned image.")

    elif ext == "docx":
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(io.BytesIO(content))
            paragraphs = []
            for para in doc.paragraphs:
                if para.text.strip():
                    paragraphs.append(para.text)
            # Also extract from tables
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            paragraphs.append(cell.text)
            text = "\n".join(paragraphs)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read DOCX: {str(e)}")

    else:
        # Plain text
        text = content.decode("utf-8", errors="ignore")

    text = text.strip()
    if not text or len(text) < 30:
        raise HTTPException(
            status_code=400,
            detail="Could not extract text from this file. For scanned PDFs, please copy-paste the text instead."
        )

    prompt = f"""File name: {filename}
File type: {ext.upper()}

Document content:
{text[:14000]}

Extract every field exactly as it appears. Do not change job titles, industries, or add information not in the document."""

    try:
        raw = await ccj(SYSTEM_EXTRACT, [{"role": "user", "content": prompt}], max_tokens=4096)
        resume = _json.loads(raw)
        return {"resume": resume, "chars_extracted": len(text), "pages": text.count("\n\n")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI parsing failed: {str(e)}")


class ResumeEditRequest(BaseModel):
    instruction: str
    current_resume: dict


@router.post("/edit")
async def edit_resume_route(req: ResumeEditRequest):
    try:
        updated = await edit_resume_with_instruction(req.instruction, req.current_resume)
        return {"resume": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-jd")
async def fetch_jd_route(req: FetchJDRequest):
    """Fetch a job description or LinkedIn profile from a URL."""
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL — must start with http:// or https://")

    # Route LinkedIn URLs through the dedicated LinkedIn scraper
    if "linkedin.com/in/" in url:
        from services.linkedin_scraper import fetch_profile_text
        text = await fetch_profile_text(url)
        if text and len(text) > 50:
            return {"text": text, "title": "LinkedIn Profile", "source": "linkedin_scraper"}
        raise HTTPException(status_code=422, detail="Could not extract profile data from LinkedIn. Please paste your profile content manually.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timed out while fetching the URL.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Remote server returned {e.response.status_code}.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {str(e)}")

    soup = BeautifulSoup(response.text, "lxml")

    # Remove script, style, nav, footer, header, and other non-content tags
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "meta", "link"]):
        tag.decompose()

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Try to find the main content area with common job description selectors
    main_content = None
    for selector in [
        "[class*='job-description']",
        "[class*='jobDescription']",
        "[class*='description']",
        "[id*='job-description']",
        "[id*='jobDescription']",
        "article",
        "main",
        "[role='main']",
    ]:
        found = soup.select_one(selector)
        if found:
            main_content = found
            break

    if main_content:
        text = main_content.get_text(separator="\n", strip=True)
    else:
        body = soup.find("body")
        text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

    # Clean up excessive blank lines
    lines = [line.strip() for line in text.splitlines()]
    cleaned_lines = []
    prev_blank = False
    for line in lines:
        if line:
            cleaned_lines.append(line)
            prev_blank = False
        elif not prev_blank:
            cleaned_lines.append("")
            prev_blank = True

    text = "\n".join(cleaned_lines).strip()

    # Truncate to a reasonable size (approx 8000 chars) to avoid overwhelming the AI
    if len(text) > 8000:
        text = text[:8000] + "\n\n[Content truncated...]"

    return {"text": text, "title": title}
