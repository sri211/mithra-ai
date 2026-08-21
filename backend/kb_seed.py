"""
Mithra chatbot knowledge base — curated Indian-jobs data + how-to-use-Mithra.
Extend freely: each entry is {id, triggers[], answer(markdown), category, tags[]}.
Admin-added / feedback-learned entries live in the DB and merge on top of these.

Salary figures are indicative INR ranges for the Indian market (2026), annual CTC.
"""

KB_SEED = [
    # ── About Mithra ──────────────────────────────────────────────────────────
    {"id": "what_is_mithra", "category": "mithra",
     "tags": ["mithra", "help", "features"],
     "triggers": ["what is mithra", "what can you do", "what does mithra do", "help", "features",
                  "what are you", "how can you help me", "what all can i do here"],
     "answer": "I'm **Mithra**, your AI career companion. Here's what you can do:\n"
               "• **Resume Builder** — build or upload your resume\n"
               "• **Resume Score** — free ATS check (0 credits)\n"
               "• **Resume Adaptor** — tailor your resume to any job\n"
               "• **Job Finder** — real jobs matched to your resume\n"
               "• **Company Intel** — research any company before you apply\n"
               "• **Auto-Apply** — assistant that fills applications for you\n"
               "• **Interview Prep** — mock questions + feedback\n"
               "• **Tracker** — kanban board for every application\n\n"
               "Ask me about salaries, top companies, skills, or how any of these work!"},

    # ── Credits ───────────────────────────────────────────────────────────────
    {"id": "credits_how", "category": "mithra",
     "tags": ["credits", "cost", "pricing", "coins"],
     "triggers": ["how do credits work", "what are credits", "credit cost", "how much does each feature cost",
                  "how many credits", "credit system", "what costs credits", "coins"],
     "answer": "Mithra runs on **credits** that refresh monthly:\n"
               "• Free: 60/mo · Pro (₹198): 300/mo · Elite (₹498): 800/mo\n\n"
               "Per-action cost: Resume Adapt **25** · AI Resume Build **15** · Interview Session **15** · "
               "Cover Letter **5** · Answer Feedback **3** · Job Search **2** · "
               "PDF Download **2** · Chat **FREE** · **Resume Score is FREE**.\n\n"
               "Out of credits? Grab a top-up (₹99 = 120, ₹199 = 280) on [Pricing](/pricing)."},

    # ── Feature how-tos ───────────────────────────────────────────────────────
    {"id": "howto_resume_builder", "category": "mithra",
     "tags": ["resume", "builder", "create resume"],
     "triggers": ["how to build a resume", "how do i make a resume", "resume builder", "create resume",
                  "build my cv", "upload resume", "how to add my resume"],
     "answer": "Go to **[Resume Builder](/resume-builder)**. Three ways in:\n"
               "1. **Upload** your existing PDF/DOCX — Mithra extracts it and even matches the template.\n"
               "2. **Chat** — answer a few questions and it drafts one.\n"
               "3. **Form** — fill sections directly.\n\n"
               "Then click **💾 Save Resume** so it's on your account and usable by Resume Adaptor and Auto-Apply."},

    {"id": "howto_adaptor", "category": "mithra",
     "tags": ["adaptor", "tailor", "ats"],
     "triggers": ["how does resume adaptor work", "how to adapt my resume", "tailor resume to job",
                  "resume adaptor", "adapt resume", "match resume to job description"],
     "answer": "**[Resume Adaptor](/resume-adaptor)** rewrites your resume for a specific job. Give it a job "
               "3 ways: **Paste JD**, **Job URL**, or **Company + Role**. It raises your ATS score, matches "
               "keywords, and suggests bullet rewrites you can accept one-by-one. Costs 25 credits."},

    {"id": "howto_job_finder", "category": "mithra",
     "tags": ["job finder", "find jobs", "search jobs"],
     "triggers": ["how does job finder work", "how to find jobs", "search for jobs", "job finder",
                  "find jobs for my resume", "filter jobs"],
     "answer": "**[Job Finder](/job-finder)** shows real listings matched to your resume. Filter by "
               "**platform** (LinkedIn, Naukri, Indeed…), **company size** (small/mid/large), **salary**, "
               "**remote**, and even a **specific company**. Each job gets an AI match score against your "
               "profile. Click **Find Jobs For My Resume** for the best matches. Costs 2 credits/search."},

    {"id": "howto_auto_apply", "category": "mithra",
     "tags": ["auto apply", "extension", "apply"],
     "triggers": ["how does auto apply work", "auto apply", "how to apply automatically",
                  "does auto apply work on linkedin", "apply for me", "browser extension"],
     "answer": "**Auto-Apply** fills job applications for you. Two paths:\n"
               "• **In-app Auto-Apply** — works on company career pages that don't require login.\n"
               "• **Mithra browser extension** — the reliable way for **LinkedIn, Naukri, Myntra** etc., "
               "because it runs in *your own logged-in browser* (no bot walls). Install it from the extension "
               "folder, save a resume, and click **Auto-Fill & Apply** on any job.\n\n"
               "You always review before anything is submitted."},

    {"id": "howto_company_intel", "category": "mithra",
     "tags": ["company intel", "research company"],
     "triggers": ["how does company intel work", "research a company", "company intel", "company details",
                  "know about a company", "glassdoor"],
     "answer": "**[Company Intel](/company-intel)** gives you a full dossier on any employer — founders, HQ, "
               "size, culture, interview process, honest pros & cons, and pay positioning. Great before an "
               "interview. First lookup costs 2 credits; after that it's cached free."},

    {"id": "howto_interview", "category": "mithra",
     "tags": ["interview prep", "mock interview"],
     "triggers": ["how does interview prep work", "interview prep", "mock interview", "practice interview",
                  "prepare for interview on mithra"],
     "answer": "**[Interview Prep](/interview-prep)** generates realistic questions for your target role + "
               "company in multiple formats (voice, MCQ, coding), then scores your answers and gives a full "
               "**Placement Readiness Report** at the end. Costs 15 credits for a session, 3 per answer evaluated."},

    {"id": "howto_tracker", "category": "mithra",
     "tags": ["tracker", "applications board"],
     "triggers": ["how does tracker work", "tracker", "track my applications", "application board", "kanban"],
     "answer": "**[Tracker](/tracker)** is a kanban board for every application — Saved → Applied → Screening → "
               "Interview → Offer. Jobs auto-add when you apply via Job Finder or Auto-Apply, and you can add "
               "any application manually. Free to use."},

    {"id": "howto_pricing", "category": "mithra",
     "tags": ["pricing", "upgrade", "plans"],
     "triggers": ["how to upgrade", "pricing", "plans", "how much is pro", "cost of elite", "subscription price"],
     "answer": "**Plans:** Free ₹0 (60 credits/mo) · **Pro ₹198** (300 credits/mo) · **Elite ₹498** "
               "(800 credits/mo + Company Intelligence). One-time top-ups: ₹99 = 120 credits, ₹199 = 280. "
               "See [Pricing](/pricing)."},

    # ── Indian salaries by role ───────────────────────────────────────────────
    {"id": "salary_swe", "category": "salary",
     "tags": ["salary", "developer", "software engineer", "sde"],
     "triggers": ["software engineer salary", "developer salary in india", "sde salary", "how much do developers earn",
                  "programmer salary", "software developer pay", "coding job salary"],
     "answer": "**Software Engineer (India, annual CTC):**\n"
               "• Fresher (0-2 yr): ₹4-12 LPA (product cos 12-30 LPA)\n"
               "• Mid (3-5 yr): ₹12-28 LPA\n"
               "• Senior (6-9 yr): ₹28-50 LPA\n"
               "• Lead/Staff (10+ yr): ₹45-90 LPA\n\n"
               "Product companies (Google, Microsoft, startups) pay 2-4× IT services (TCS, Infosys). "
               "Use **Job Finder** to see real salaries and **Company Intel** to check a company's pay reputation."},

    {"id": "salary_data", "category": "salary",
     "tags": ["salary", "data scientist", "data analyst", "machine learning"],
     "triggers": ["data scientist salary", "data analyst salary india", "ml engineer salary", "analytics salary",
                  "how much does a data scientist earn"],
     "answer": "**Data roles (India, annual CTC):**\n"
               "• Data Analyst — Fresher ₹4-8 LPA · Mid ₹8-16 LPA · Senior ₹16-30 LPA\n"
               "• Data Scientist — Fresher ₹6-14 LPA · Mid ₹14-30 LPA · Senior ₹30-60 LPA\n"
               "• ML Engineer — Mid ₹15-35 LPA · Senior ₹35-70 LPA\n\n"
               "Skills that push pay up: Python, SQL, ML, deep learning, cloud, and domain expertise."},

    {"id": "salary_pm", "category": "salary",
     "tags": ["salary", "product manager"],
     "triggers": ["product manager salary", "pm salary india", "how much does a product manager earn",
                  "product management pay"],
     "answer": "**Product Manager (India, annual CTC):**\n"
               "• Associate PM (0-2 yr): ₹10-20 LPA\n"
               "• PM (3-6 yr): ₹20-45 LPA\n"
               "• Senior PM (6-10 yr): ₹40-80 LPA\n"
               "• Group PM / Director: ₹80 LPA-1.5 Cr\n\n"
               "Top payers: Google, Microsoft, Flipkart, Razorpay, Swiggy, CRED."},

    {"id": "salary_marketing", "category": "salary",
     "tags": ["salary", "marketing", "digital marketing"],
     "triggers": ["marketing salary india", "digital marketing salary", "marketing manager pay",
                  "how much do marketers earn", "seo salary", "performance marketing salary"],
     "answer": "**Marketing (India, annual CTC):**\n"
               "• Executive/Associate (0-2 yr): ₹3-7 LPA\n"
               "• Manager (3-6 yr): ₹8-20 LPA\n"
               "• Senior/Head (7-12 yr): ₹20-50 LPA\n\n"
               "Performance marketing, growth, and marketing analytics pay a premium over traditional/brand roles."},

    {"id": "salary_sales", "category": "salary",
     "tags": ["salary", "sales", "business development"],
     "triggers": ["sales salary india", "business development salary", "bd salary", "sales manager pay",
                  "how much do sales people earn", "account manager salary"],
     "answer": "**Sales / Business Development (India, annual CTC + incentives):**\n"
               "• Executive/BDE (0-2 yr): ₹3-6 LPA + incentives\n"
               "• Manager (3-6 yr): ₹8-18 LPA + incentives\n"
               "• Senior/AVP (7-12 yr): ₹20-45 LPA + incentives\n\n"
               "SaaS and enterprise B2B sales pay the highest incentives; key-account roles are lucrative."},

    {"id": "salary_hr", "category": "salary",
     "tags": ["salary", "human resources", "hr"],
     "triggers": ["hr salary india", "human resources salary", "hr manager pay", "recruiter salary",
                  "talent acquisition salary"],
     "answer": "**HR (India, annual CTC):**\n"
               "• HR Executive/Recruiter (0-2 yr): ₹3-6 LPA\n"
               "• HRBP/Manager (3-7 yr): ₹8-20 LPA\n"
               "• Senior/Head HR (8-15 yr): ₹20-60 LPA\n\n"
               "HRBP and Talent Acquisition at product/tech firms pay more than generalist roles."},

    {"id": "salary_finance", "category": "salary",
     "tags": ["salary", "finance", "accounting", "ca"],
     "triggers": ["finance salary india", "accountant salary", "ca salary", "financial analyst salary",
                  "how much do finance professionals earn"],
     "answer": "**Finance & Accounting (India, annual CTC):**\n"
               "• Accountant (0-3 yr): ₹3-7 LPA\n"
               "• Financial Analyst (2-5 yr): ₹6-16 LPA\n"
               "• CA (fresher): ₹8-14 LPA · CA (5+ yr): ₹18-40 LPA\n"
               "• Finance Manager/Controller: ₹20-60 LPA\n\n"
               "Investment banking and FP&A at MNCs sit at the top of the range."},

    # ── Top companies ─────────────────────────────────────────────────────────
    {"id": "top_it_companies", "category": "companies",
     "tags": ["companies", "it", "top employers"],
     "triggers": ["top it companies in india", "best companies to work for", "top employers india",
                  "which companies hire the most", "big tech in india"],
     "answer": "**Top tech employers hiring in India:**\n"
               "• **Product/Global:** Google, Microsoft, Amazon, Meta, Adobe, Uber, Walmart Global Tech, Salesforce\n"
               "• **Indian product/unicorns:** Flipkart, Swiggy, Zomato, Razorpay, CRED, PhonePe, Zerodha, Meesho, Nykaa\n"
               "• **IT services:** TCS, Infosys, Wipro, HCL, Tech Mahindra, Accenture, Cognizant, Capgemini\n\n"
               "Use **Company Intel** to research culture, pay, and interviews for any of them."},

    {"id": "top_fintech", "category": "companies",
     "tags": ["companies", "fintech"],
     "triggers": ["top fintech companies india", "best fintech to work", "fintech jobs", "which fintech hire"],
     "answer": "**Leading fintechs in India:** Razorpay, CRED, PhonePe, Paytm, Zerodha, Groww, Jupiter, "
               "Fi Money, BharatPe, Juspay, Zeta, KreditBee, slice. Strong pay, fast-paced. "
               "Research any with **Company Intel** before applying."},

    {"id": "startup_vs_mnc", "category": "career",
     "tags": ["startup", "mnc", "career choice"],
     "triggers": ["startup or mnc", "should i join a startup", "startup vs corporate", "mnc vs startup",
                  "is startup good for career"],
     "answer": "**Startup vs MNC — quick take:**\n"
               "• **Startup:** faster growth, broad ownership, equity upside, but higher risk and longer hours.\n"
               "• **MNC:** stability, structured learning, brand value, better work-life, slower progression.\n\n"
               "Early career: a good startup accelerates learning; if you value stability or are risk-averse, "
               "an MNC is safer. Check any company's reality in **Company Intel**."},

    # ── Skills ────────────────────────────────────────────────────────────────
    {"id": "in_demand_skills", "category": "skills",
     "tags": ["skills", "in demand", "learn"],
     "triggers": ["in demand skills 2026", "what skills should i learn", "top skills for jobs",
                  "which skills are hot", "skills to get a job", "future proof skills"],
     "answer": "**High-demand skills in India (2026):**\n"
               "• **Tech:** AI/ML, GenAI & LLMs, Python, cloud (AWS/GCP/Azure), data engineering, "
               "cybersecurity, full-stack, DevOps\n"
               "• **Data:** SQL, analytics, Power BI/Tableau, ML\n"
               "• **Business:** product management, digital & performance marketing, growth, UX\n"
               "• **Everywhere:** communication, problem-solving, AI-tool fluency\n\n"
               "Add the ones matching your target role to your resume, then run **Resume Score**."},

    # ── Interview ─────────────────────────────────────────────────────────────
    {"id": "interview_prep_tips", "category": "interview",
     "tags": ["interview", "preparation", "tips"],
     "triggers": ["how to prepare for an interview", "interview tips", "how to crack an interview",
                  "interview preparation", "ace an interview", "interview advice"],
     "answer": "**Interview prep in 5 steps:**\n"
               "1. Research the company (use **Company Intel**) and the role.\n"
               "2. Prepare STAR stories for your top 4-5 achievements.\n"
               "3. Nail \"Tell me about yourself\" (60-90s: present → past → future).\n"
               "4. Prepare 3 smart questions to ask them.\n"
               "5. Do a mock run in **[Interview Prep](/interview-prep)** for role-specific questions + feedback.\n\n"
               "Practise out loud — it's the single biggest improvement."},

    {"id": "tell_me_about_yourself", "category": "interview",
     "tags": ["interview", "tell me about yourself"],
     "triggers": ["tell me about yourself", "how to answer tell me about yourself", "introduce yourself in interview",
                  "self introduction interview"],
     "answer": "**\"Tell me about yourself\" — the Present-Past-Future formula (60-90s):**\n"
               "• **Present:** your current role + a headline achievement.\n"
               "• **Past:** the 1-2 experiences that led you here and built relevant skills.\n"
               "• **Future:** why *this* role/company is the logical next step.\n\n"
               "Keep it professional, tie everything to the job, and end with enthusiasm for the role."},

    {"id": "salary_expectation_q", "category": "interview",
     "tags": ["interview", "salary expectation", "negotiation"],
     "triggers": ["what are your salary expectations", "how to answer salary expectation", "expected ctc answer",
                  "how to negotiate salary", "salary negotiation tips"],
     "answer": "**Salary expectation question:**\n"
               "• Research the market range first (I can tell you — just ask 'X salary in India').\n"
               "• Give a **range**, not a single number, anchored slightly above market: \"Based on my "
               "experience and market rates, I'm looking at ₹X-Y LPA, but I'm open depending on the total package.\"\n"
               "• When you have an offer, negotiate on the total (base + bonus + ESOPs), not just base. "
               "A competing offer is your strongest lever."},

    # ── Resume ────────────────────────────────────────────────────────────────
    {"id": "resume_ats_tips", "category": "resume",
     "tags": ["resume", "ats", "tips"],
     "triggers": ["how to make my resume ats friendly", "ats resume tips", "resume tips", "improve my resume",
                  "resume keywords", "beat the ats", "resume best practices"],
     "answer": "**ATS-friendly resume checklist:**\n"
               "• Mirror keywords from the job description (skills, tools, titles).\n"
               "• Quantify bullets: \"Increased X by Y% by doing Z.\"\n"
               "• Simple layout, standard headings, no tables/graphics for the ATS version.\n"
               "• 1 page (≤5 yrs exp), 2 pages max.\n"
               "• Strong 2-3 line summary up top.\n\n"
               "Run **[Resume Score](/resume-score)** (free) for a 7-dimension check, then **Resume Adaptor** "
               "to auto-match a specific job."},

    {"id": "resume_bullets", "category": "resume",
     "tags": ["resume", "bullets", "achievements"],
     "triggers": ["how to write resume bullet points", "resume achievements", "how to describe experience",
                  "make my bullets stronger", "action verbs resume"],
     "answer": "**Write bullets that get interviews** — use the formula **Action verb + what you did + measurable result:**\n"
               "• ❌ \"Responsible for managing social media.\"\n"
               "• ✅ \"Grew Instagram engagement 60% in 4 months by launching a weekly reels series.\"\n\n"
               "Start with strong verbs (Led, Built, Grew, Reduced, Launched), and add numbers wherever you can — "
               "revenue, %, time saved, team size, users."},

    # ── Career FAQ ────────────────────────────────────────────────────────────
    {"id": "job_no_experience", "category": "career",
     "tags": ["fresher", "no experience", "first job"],
     "triggers": ["how to get a job with no experience", "fresher jobs", "first job tips", "no experience job",
                  "how to get first job", "entry level jobs"],
     "answer": "**Landing your first job (no experience):**\n"
               "1. Build 2-3 **projects** or take on freelance/internships — they count as experience.\n"
               "2. Get certifications in your target skill (adds keywords + credibility).\n"
               "3. A sharp, ATS-optimized resume — run **Resume Score** and **Resume Adaptor**.\n"
               "4. Apply widely via **Job Finder** filtered to 0-2 yr roles.\n"
               "5. Network on LinkedIn and ask for referrals — referred candidates get 5-10× more interviews.\n\n"
               "Consistency beats perfection — apply to 5-10 relevant roles daily."},

    {"id": "career_switch", "category": "career",
     "tags": ["career change", "switch", "transition"],
     "triggers": ["how to switch careers", "career change tips", "move to a new field", "changing careers",
                  "transition to tech", "switch domains"],
     "answer": "**Switching careers:**\n"
               "1. Identify **transferable skills** from your current role.\n"
               "2. Close the gap with focused learning/certs in the target field.\n"
               "3. Build a project or two to prove capability.\n"
               "4. Rewrite your resume around the *target* role — **Resume Adaptor** is built for exactly this.\n"
               "5. Target companies open to career-changers, and lead with your transferable wins.\n\n"
               "Expect a possible short-term pay adjustment that pays off within 1-2 years."},

    {"id": "notice_period", "category": "career",
     "tags": ["notice period", "resignation"],
     "triggers": ["notice period in india", "what is standard notice period", "how long is notice period",
                  "buy out notice period", "serving notice"],
     "answer": "**Notice period norms in India:**\n"
               "• Startups: 15-30 days · Mid-size: 30-60 days · Large IT (TCS/Infosys etc.): 60-90 days.\n"
               "• Many new employers will wait; some offer a **buyout** of your notice.\n"
               "• Negotiate early leave if you have unused leave to adjust.\n\n"
               "In interviews, state your notice honestly — it rarely costs you the offer."},

    {"id": "best_job_portals", "category": "career",
     "tags": ["job portals", "where to apply"],
     "triggers": ["best job portals in india", "where to search for jobs", "top job sites", "job websites india",
                  "which portal for jobs"],
     "answer": "**Top job portals in India:** LinkedIn (best for referrals + product roles), Naukri (widest "
               "coverage), Indeed, Instahyre & Wellfound (startups/tech), Foundit (ex-Monster), and company "
               "career pages directly.\n\n"
               "**Job Finder** here pulls from these and matches them to your resume so you skip the noise."},

    {"id": "referral_importance", "category": "career",
     "tags": ["referral", "networking"],
     "triggers": ["how important are referrals", "how to get referrals", "networking for jobs", "employee referral",
                  "does networking help"],
     "answer": "**Referrals are the #1 hiring channel** — referred candidates are 5-10× more likely to get an "
               "interview. How to get them:\n"
               "1. Find 2nd-degree connections at your target company on LinkedIn.\n"
               "2. Send a short, specific message: who you are, the role, why you fit (2-3 lines).\n"
               "3. Attach your tailored resume (use **Resume Adaptor**).\n\n"
               "Even a cold, polite referral request has a surprisingly high hit rate."},

    {"id": "remote_jobs_india", "category": "career",
     "tags": ["remote", "work from home"],
     "triggers": ["remote jobs in india", "work from home jobs", "wfh jobs", "how to find remote work",
                  "remote friendly companies"],
     "answer": "**Remote work in India** is common in tech, content, design, and customer roles. Filter "
               "**Job Finder → Remote** to see only remote/hybrid listings. Remote-friendly employers include "
               "GitLab, Zapier, Automattic, and many Indian startups. For remote roles, emphasize written "
               "communication and self-management on your resume."},
]
