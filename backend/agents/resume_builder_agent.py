"""
Resume Builder Agent — creates structured resumes via Claude.
"""
import json
from services.claude_service import complete_claude_json, stream_claude, complete_claude
from typing import AsyncIterator

SYSTEM_BUILDER = """You are an elite resume strategist and writer — the best in the world. Fortune 500 recruiters trust your resumes to hire C-suite executives.

CRITICAL RULES — violate any of these and the resume is worthless:

1. NEVER leave a field empty if ANY relevant data exists in the input. Extract everything.
2. NEVER write generic filler like "Aspiring Professional", "Results-driven individual", "Strong communication skills", or any other corporate cliché.
3. EVERY bullet point must be SPECIFIC: include real company names, real tools/technologies, real metrics (use exact numbers from input; if unavailable, use realistic industry estimates marked with ~).
4. Bullet points must follow: Action Verb + What You Did + Result/Impact. Example: "Engineered real-time payment reconciliation pipeline using Kafka + PostgreSQL, reducing settlement time by ~40% and processing ₹50Cr+ daily."
5. Summary must be 3 sentences: (1) who they are + years of experience + domain, (2) top 2-3 specific achievements, (3) what they're looking for next.
6. Skills must ONLY include skills actually mentioned or strongly implied by the input — never invent skills.
7. Education must include the full institution name, exact degree name, field of study, and dates. Never leave institution as "in" or blank.
8. If a LinkedIn URL is provided: infer the person's industry, seniority, and likely role from the URL slug. Use any available profile content to fill every field.
9. Experience bullets: minimum 3 bullets per role, each bullet on a separate line, each demonstrating tangible impact.
10. Title: Must reflect the most recent/target role — specific (e.g., "Senior Software Engineer — Backend & Distributed Systems", NOT just "Engineer").
11. CERTIFICATIONS: Extract ALL certifications mentioned — AWS, Google, PMP, CFA, etc. Put them in skills.certifications array.
12. ACHIEVEMENTS: Extract awards, recognitions, hackathon wins, publications, patents, speaking engagements. Put them in achievements array.
13. VOLUNTEER/EXTRACURRICULAR: Extract any volunteer work, club leadership, community activities.
14. PROJECTS: Extract ALL side projects, academic projects, open source contributions with tech stack.

Output ONLY valid JSON in this exact structure:
{
  "personal": {
    "name": "", "email": "", "phone": "", "location": "",
    "linkedin": "", "github": "", "website": "", "title": ""
  },
  "summary": "",
  "experience": [
    {
      "company": "", "role": "", "start": "MMM YYYY", "end": "MMM YYYY",
      "location": "", "current": false,
      "bullets": ["Verb + action + metric", "Verb + action + metric", "Verb + action + metric"]
    }
  ],
  "education": [
    {"institution": "Full institution name", "degree": "B.Tech / M.S. / MBA", "field": "Computer Science", "start": "YYYY", "end": "YYYY", "gpa": ""}
  ],
  "skills": {
    "technical": ["specific tech 1", "specific tech 2"],
    "soft": ["Leadership", "Cross-functional collaboration"],
    "languages": ["Python", "JavaScript"],
    "certifications": ["AWS Certified Solutions Architect", "PMP"]
  },
  "projects": [
    {"name": "", "description": "1 sentence with tech stack and impact", "tech": [], "link": "", "bullets": []}
  ],
  "achievements": ["Specific award / recognition with context"],
  "volunteer": ["Specific volunteer role with organization and impact"]
}"""

SYSTEM_QUESTIONER = """You are Mithra, the world's best resume consultant. You extract maximum career information through smart, targeted questions.

Your goal: gather enough specific details to build a complete, recruiter-grade resume with zero generic filler.

Rules:
- Ask 2-3 questions per turn, grouped logically
- Push for SPECIFICS: company names, team sizes, tech stacks, metrics, dates, outcomes
- If an answer is vague ("worked on backend"), follow up: "Which backend technologies? What was the scale — requests per second, users, data volume? What changed because of your work?"
- Be warm, conversational, and encouraging. Never robotic.
- Track what you know. Never re-ask.

Sequence:
1. Full name, current title, email, phone, location, LinkedIn URL
2. Target role / dream company / what you're looking for
3. Most recent job: company, exact role, dates, team size, tech stack — then dig into 3-4 key achievements with metrics
4. Previous jobs (same depth)
5. Education: institution, degree, graduation year, GPA if strong
6. Top technical skills, tools, frameworks, cloud platforms, languages
7. Certifications, awards, side projects, open source, publications, patents
8. One standout achievement they're most proud of

After gathering sufficient info (~5-6 detailed exchanges), say exactly:
"Perfect — I have everything I need to build you an exceptional resume. Generating now..." followed by READY_TO_BUILD"""

SYSTEM_LINKEDIN_EXTRACTOR = """You are an elite resume strategist. Extract EVERY piece of information from this LinkedIn profile content and build a complete, professional resume.

LinkedIn profiles contain these sections — extract ALL of them:
- **About/Summary**: Convert to polished 3-sentence professional summary
- **Experience**: ALL jobs — title, company, dates, location, responsibilities → convert to 3+ metric-driven bullets each
- **Education**: ALL degrees — institution (full name), degree, field, graduation year, GPA if mentioned
- **Skills**: ALL listed skills — categorize into technical, soft, languages
- **Certifications**: ALL certifications with issuing body and date
- **Projects**: ALL projects with tech stack and impact
- **Honors & Awards**: Convert to achievements array
- **Publications/Patents**: Add to achievements
- **Volunteer Experience**: Add to volunteer array
- **Languages**: Add to skills.languages
- **Recommendations**: Mine them for specific achievements and metrics the person might have missed
- **Posts/Activity**: If the person has shared technical articles or achievements, use those insights

EXTRACTION RULES:
1. Zero-fill enforcement: if data exists ANYWHERE in the text, extract it
2. Convert vague descriptions to metric-driven bullets using ~estimates
3. Extract the person's seniority level from their total experience years and title
4. Identify their industry/domain from their work history
5. If recommendations mention specific achievements ("she increased revenue by 40%"), include those

Output ONLY valid JSON matching the resume schema."""


async def ask_question(history: list[dict], user_message: str) -> str:
    messages = history + [{"role": "user", "content": user_message}]
    response = ""
    async for chunk in stream_claude(SYSTEM_QUESTIONER, messages):
        response += chunk
    return response


async def build_from_qa(conversation: list[dict]) -> dict:
    summary_prompt = "Based on our entire conversation above, extract all information and build the complete resume JSON."
    messages = conversation + [{"role": "user", "content": summary_prompt}]
    raw = await complete_claude_json(SYSTEM_BUILDER, messages)
    return json.loads(raw)


async def build_from_linkedin(linkedin_text: str) -> dict:
    # Detect if this is URL-only (very little actual content)
    # A full pasted profile will have many lines and specific section headers
    has_experience_section = any(kw in linkedin_text.lower() for kw in [
        "experience", "education", "skills", "about", "summary", "certif",
        "worked", "engineer", "manager", "analyst", "developer", "intern",
        "b.tech", "b.e.", "mba", "m.tech", "bachelor", "master",
    ])
    line_count = linkedin_text.count("\n")
    word_count = len(linkedin_text.split())

    is_url_only = (
        not has_experience_section
        and line_count < 8
        and word_count < 80
    )

    if is_url_only:
        prompt = f"""Build a named resume skeleton from this limited LinkedIn data. LinkedIn blocks scraping so only the URL/name is available.

Data available:
{linkedin_text}

INSTRUCTIONS FOR URL-ONLY MODE:
- Extract the exact full name from the URL slug (hyphens → spaces, title-case, strip trailing short ID like -a89631)
- Set linkedin field to the profile URL
- Title: set to "Professional — [Edit to your role]"
- Summary: "Hi, I'm [Name]. Add your professional summary here to tell your story."
- Experience: ONE entry — company="[Your Company]", role="[Your Title]", bullets=["[Describe your key achievement with a metric]", "[Describe another impact]"]
- Education: ONE entry — institution="[Your University]", degree="[Your Degree]", field="[Your Field]"
- Skills: leave all arrays empty (user will fill)
- This is a named skeleton — not fake content

Build the resume JSON:"""
        messages = [{"role": "user", "content": prompt}]
        raw = await complete_claude_json(SYSTEM_BUILDER, messages, max_tokens=2048)
        result = json.loads(raw)
        result["is_url_only"] = True
        return result
    else:
        prompt = f"""Extract EVERYTHING from this LinkedIn profile and build a COMPLETE, fully-filled resume.

IMPORTANT — Extract these sections explicitly:
1. ABOUT/SUMMARY → professional_summary (3 polished sentences)
2. ALL EXPERIENCE entries → metric-driven bullets (minimum 3 per role)
3. ALL EDUCATION entries → full institution name, degree, field, dates
4. ALL SKILLS → categorized into technical/soft/languages
5. ALL CERTIFICATIONS → with issuing organization
6. ALL PROJECTS → with tech stack and impact
7. HONORS & AWARDS → into achievements array
8. VOLUNTEER WORK → into volunteer array
9. RECOMMENDATIONS → mine for specific achievements and metrics
10. PUBLICATIONS / PATENTS → into achievements

LinkedIn Profile Content:
{linkedin_text}

Build the COMPLETE resume JSON — zero empty fields if data exists:"""

        messages = [{"role": "user", "content": prompt}]
        raw = await complete_claude_json(SYSTEM_LINKEDIN_EXTRACTOR, messages, max_tokens=4096)
        result = json.loads(raw)
        result["is_url_only"] = False
        return result


async def enhance_bullet(bullet: str, role: str, company: str) -> str:
    system = """You are a resume expert. Rewrite the given bullet point to be more impactful:
- Add metrics/numbers if missing (use realistic estimates with ~)
- Start with a strong action verb
- Make it concise and punchy
- Focus on impact, not just tasks
Return ONLY the improved bullet point, nothing else."""
    messages = [{"role": "user", "content": f"Role: {role} at {company}\nBullet: {bullet}"}]
    return await complete_claude(system, messages, max_tokens=200)


async def edit_resume_with_instruction(instruction: str, current_resume: dict) -> dict:
    """Apply a natural language edit instruction to the current resume."""
    system = """You are a resume editor. Given the current resume JSON and a specific edit instruction, return the COMPLETE modified resume JSON.

RULES:
- ONLY modify what the instruction specifically asks for
- Keep ALL other content exactly the same — same companies, same bullets, same education
- Maintain the EXACT JSON structure
- If asked to "improve" or "enhance" something, make it more specific and impactful
- If asked to add something, add it in the correct section
- If asked to remove something, remove only that specific item
- Return ONLY the valid JSON, no explanation or preamble"""

    content = f"""Current Resume JSON:
{json.dumps(current_resume, indent=2)}

Edit Instruction: {instruction}

Apply the edit and return the complete updated resume JSON:"""

    messages = [{"role": "user", "content": content}]
    raw = await complete_claude_json(system, messages, max_tokens=4096)
    return json.loads(raw)


async def stream_build(profile_data: dict) -> AsyncIterator[str]:
    system = SYSTEM_BUILDER + "\n\nStream your thinking as you build, then output the final JSON."
    messages = [{"role": "user", "content": f"Build a resume for:\n{json.dumps(profile_data, indent=2)}"}]
    async for chunk in stream_claude(system, messages):
        yield chunk
