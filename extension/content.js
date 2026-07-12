// Orchestrator: injects the Mithra panel, fetches the user's profile+resume,
// runs the right site adapter, keeps a human in the loop before submit, and
// reports the application back to the Tracker.
(function () {
  const E = window.MithraEngine;
  if (!E) return;
  const { sleep, rand, isVisible, fillText, fillFileInput, findField,
          selectNative, selectCustom, clickByText, hasVisibleCaptcha } = E;

  const host = location.hostname;
  const site =
    host.includes("linkedin") ? "linkedin" :
    host.includes("naukri") ? "naukri" :
    host.includes("greenhouse") ? "greenhouse" :
    host.includes("lever") ? "lever" :
    host.includes("workday") ? "workday" :
    host.includes("indeed") ? "indeed" :
    host.includes("instahyre") ? "instahyre" :
    host.includes("foundit") ? "foundit" :
    host.includes("shine") ? "shine" : "generic";

  let profile = null, resumePdf = null, running = false;

  const bg = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));

  // ── UI: floating panel ──────────────────────────────────────────────────
  let panel, logEl;
  function ui() {
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "mithra-panel";
    panel.innerHTML = `
      <div class="mithra-hd">
        <span class="mithra-logo">✦ Mithra</span>
        <button class="mithra-x" title="Close">×</button>
      </div>
      <div class="mithra-body">
        <div class="mithra-status">Ready to auto-fill this application.</div>
        <div class="mithra-log"></div>
        <button class="mithra-go">Auto-Fill &amp; Apply</button>
        <div class="mithra-hint">You review before anything is submitted.</div>
      </div>`;
    document.documentElement.appendChild(panel);
    logEl = panel.querySelector(".mithra-log");
    panel.querySelector(".mithra-x").onclick = () => (panel.style.display = "none");
    panel.querySelector(".mithra-go").onclick = start;
  }
  function status(t) { if (panel) panel.querySelector(".mithra-status").textContent = t; }
  function log(t) { if (logEl) { const d = document.createElement("div"); d.textContent = "• " + t; logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight; } }
  function setBtn(t, disabled) { const b = panel?.querySelector(".mithra-go"); if (b) { b.textContent = t; b.disabled = !!disabled; } }

  // In-page ask (BLOCKAGE #4 — novel screening questions). Caches answers.
  function ask(question, placeholder = "") {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "mithra-ask";
      box.innerHTML = `<div class="mithra-ask-q"></div>
        <input class="mithra-ask-in" placeholder="${placeholder || "Type your answer…"}"/>
        <div class="mithra-ask-row"><button class="mithra-ask-ok">Fill</button>
        <button class="mithra-ask-skip">Skip</button></div>`;
      box.querySelector(".mithra-ask-q").textContent = question;
      panel.querySelector(".mithra-body").appendChild(box);
      const input = box.querySelector(".mithra-ask-in");
      input.focus();
      const done = (v) => { box.remove(); resolve(v); };
      box.querySelector(".mithra-ask-ok").onclick = () => done(input.value.trim());
      box.querySelector(".mithra-ask-skip").onclick = () => done("");
      input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim()); };
    });
  }

  async function cachedAnswer(key, question, placeholder) {
    const store = (await chrome.storage.local.get("answers")).answers || {};
    if (store[key]) return store[key];
    const v = await ask(question, placeholder);
    if (v) { store[key] = v; await chrome.storage.local.set({ answers: store }); }
    return v;
  }

  // ── Generic field fill (works on most ATS forms) ────────────────────────
  async function fillCommon() {
    let n = 0;
    const p = profile;
    const map = [
      [["first name", "given name", "fname"], p.first_name],
      [["last name", "surname", "family name", "lname"], p.last_name],
      [["full name", "your name"], p.name, ["first", "last", "company", "user name", "username"]],
      [["email", "e-mail"], p.email],
      [["phone", "mobile", "contact number"], p.phone],
      [["linkedin"], p.linkedin],
      [["github"], p.github],
      [["website", "portfolio"], p.website],
      [["city"], p.city],
      [["location", "current location", "based"], p.location, ["preferred", "relocat"]],
      [["headline", "current title", "job title"], p.headline],
      [["expected", "expected ctc", "expected salary"], p.answers.expected_ctc],
      [["current ctc", "current salary"], p.answers.current_ctc],
      [["notice"], p.answers.notice_period],
      [["experience", "total experience", "years of exp"], String(p.years_experience || "")],
    ];
    for (const [hints, value, exclude] of map) {
      if (!value) continue;
      const el = findField(hints, exclude ? { exclude } : {});
      if (el && !el.value) { if (await fillText(el, value)) { n++; await sleep(rand(80, 200)); } }
    }
    return n;
  }

  async function fillDropdowns() {
    let n = 0;
    // native selects
    for (const sel of [...document.querySelectorAll("select")].filter(isVisible)) {
      if (sel.value && sel.selectedIndex > 0) continue;
      const t = E.labelTextFor(sel);
      let kw = [];
      if (/qualif|education|degree/.test(t)) kw = [profile.headline, "bachelor", "graduate"];
      else if (/location|city/.test(t)) kw = [profile.city, "bangalore"];
      else if (/experience/.test(t)) kw = [String(profile.years_experience)];
      else if (/notice/.test(t)) kw = ["30", "immediate", "1 month"];
      else if (/relocat/.test(t)) kw = ["yes"];
      if (await selectNative(sel, kw)) n++;
    }
    // custom dropdowns
    const triggers = [...document.querySelectorAll("[role=combobox], [class*='select__control'], [aria-haspopup='listbox']")].filter(isVisible);
    for (const tr of triggers.slice(0, 8)) {
      const t = E.labelTextFor(tr);
      let kw = [];
      if (/qualif|education|degree/.test(t)) kw = [profile.headline, "bachelor", "graduate"];
      else if (/location|city/.test(t)) kw = [profile.city, "bangalore"];
      else if (/department|function/.test(t)) kw = [profile.headline, "general"];
      else if (/notice/.test(t)) kw = ["30", "immediate"];
      if (kw.length && await selectCustom(tr, kw)) n++;
    }
    return n;
  }

  async function tickConsents() {
    let n = 0;
    for (const cb of [...document.querySelectorAll("input[type=checkbox]")].filter(isVisible)) {
      if (cb.required && !cb.checked) { cb.click(); n++; await sleep(rand(80, 180)); }
    }
    // Yes/No radios: answer "No" to fresher for experienced, else affirmative
    const seen = new Set();
    for (const r of [...document.querySelectorAll("input[type=radio]")].filter(isVisible)) {
      if (seen.has(r.name)) continue;
      const t = (E.labelTextFor(r) + " " + (r.value || "")).toLowerCase();
      const fresher = /fresher/.test(t) && profile.years_experience > 0;
      const want = fresher ? /(^|\W)no(\W|$)|false/ : /yes|agree|authorized|available|true/;
      if (want.test(t) && !r.checked) { r.click(); seen.add(r.name); n++; await sleep(rand(80, 180)); }
    }
    return n;
  }

  async function uploadResume() {
    const inputs = [...document.querySelectorAll("input[type=file]")];
    if (!inputs.length) return false;
    if (!resumePdf) {
      const r = await bg({ type: "GET_RESUME_PDF" });
      if (r?.ok) resumePdf = r;
    }
    if (!resumePdf?.base64) return false;
    for (const inp of inputs.slice(0, 3)) {
      if (await fillFileInput(inp, resumePdf.base64, resumePdf.filename || "resume.pdf")) {
        log("Attached your resume");
        return true;
      }
    }
    return false;
  }

  // Free-text screening questions we can't infer — ask the user (cached).
  async function answerScreening() {
    const areas = [...document.querySelectorAll("textarea")].filter(isVisible);
    for (const ta of areas.slice(0, 4)) {
      if (ta.value) continue;
      const q = (E.labelTextFor(ta) || "your answer").trim().slice(0, 100);
      if (/cover|message|why|describe|tell us/.test(q)) {
        const val = profile.answers.why_this_role || await cachedAnswer("cover_" + q.slice(0, 20), "Answer for: " + q);
        if (val) await fillText(ta, val);
      }
    }
    // Number/short-text required inputs still empty → ask once each
    const empties = [...document.querySelectorAll("input[required]:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file])")]
      .filter((el) => isVisible(el) && !el.value).slice(0, 4);
    for (const el of empties) {
      const q = (E.labelTextFor(el) || "field").trim().slice(0, 80);
      const val = await cachedAnswer("q_" + q.slice(0, 24), "The form asks: " + q);
      if (val) await fillText(el, val);
    }
  }

  async function fillCurrentView() {
    let n = 0;
    n += await fillCommon();
    n += await fillDropdowns();
    await uploadResume();
    n += await tickConsents();
    await answerScreening();
    return n;
  }

  // ── LinkedIn Easy Apply: multi-step modal (BLOCKAGE #5) ─────────────────
  async function runLinkedIn() {
    // Open Easy Apply if not already in the modal
    if (!document.querySelector(".jobs-easy-apply-modal, [data-test-modal]")) {
      if (!clickByText(["Easy Apply"])) {
        status("No Easy Apply on this job — it applies on the company site. Use the company page.");
        return { reached: false };
      }
      await sleep(1500);
    }
    for (let step = 0; step < 8; step++) {
      if (hasVisibleCaptcha()) { status("Please solve the CAPTCHA, then click Auto-Fill again."); return { reached: true, captcha: true }; }
      const filled = await fillCurrentView();
      log(`Filled ${filled} field(s) on step ${step + 1}`);
      await sleep(rand(500, 900));
      // Review / Submit present?
      const submitBtn = [...document.querySelectorAll("button")].find(
        (b) => isVisible(b) && /submit application/i.test(b.innerText || ""));
      if (submitBtn) return { reached: true, submitBtn };
      // else advance
      if (!clickByText(["Next", "Review", "Continue to next step"])) break;
      await sleep(rand(700, 1100));
    }
    return { reached: true };
  }

  async function runGeneric() {
    if (hasVisibleCaptcha()) { status("Please solve the CAPTCHA, then click Auto-Fill again."); return { reached: true, captcha: true }; }
    // Try to open an application form if there's an Apply button and no form yet
    const hasForm = document.querySelector("input[type=file], form input[type=email], form input[type=tel]");
    if (!hasForm) { clickByText(["Apply now", "Apply", "I'm interested"]); await sleep(1500); }
    const filled = await fillCurrentView();
    log(`Filled ${filled} field(s)`);
    const submitBtn = [...document.querySelectorAll("button, input[type=submit]")].find(
      (b) => isVisible(b) && /submit|apply|send application/i.test(b.innerText || b.value || ""));
    return { reached: true, submitBtn };
  }

  function jobMeta() {
    const pick = (sels) => { for (const s of sels) { const el = document.querySelector(s); if (el && el.innerText) return el.innerText.trim(); } return ""; };
    const title = pick([".jobs-unified-top-card__job-title", "h1", ".topcard__title", ".jd-header-title"]) || document.title;
    const company = pick([".jobs-unified-top-card__company-name", ".topcard__org-name-link", ".jd-header-comp-name", "[class*='company']"]) || host;
    return { title: title.slice(0, 140), company: company.slice(0, 100) };
  }

  async function start() {
    if (running) return;
    running = true;
    setBtn("Working…", true);
    logEl.innerHTML = "";
    try {
      const st = await bg({ type: "GET_STATUS" });
      if (!st?.connected) {
        status("Not connected. Open mithraai.in, sign in, then click the Mithra icon → Connect.");
        setBtn("Auto-Fill & Apply", false); running = false; return;
      }
      if (!profile) {
        const pr = await bg({ type: "GET_PROFILE" });
        if (!pr?.ok) { status("Couldn't load your Mithra profile. Build a resume first."); setBtn("Auto-Fill & Apply", false); running = false; return; }
        profile = pr.profile;
      }
      status("Filling the application…");
      const runner = site === "linkedin" ? runLinkedIn : runGeneric;
      const result = await runner();

      if (result.captcha) { setBtn("Auto-Fill & Apply", false); running = false; return; }
      if (!result.reached) { setBtn("Auto-Fill & Apply", false); running = false; return; }

      // Confirm-before-submit — the human stays in control (also lowers ban risk)
      status("Review the form above. Ready to submit?");
      const meta = jobMeta();
      const confirmBox = document.createElement("div");
      confirmBox.className = "mithra-confirm";
      confirmBox.innerHTML = `<button class="mithra-submit">✓ Submit application</button>
        <button class="mithra-manual">I'll submit myself</button>`;
      panel.querySelector(".mithra-body").appendChild(confirmBox);

      const finish = async (didSubmit) => {
        confirmBox.remove();
        if (didSubmit && result.submitBtn) {
          result.submitBtn.scrollIntoView({ block: "center" });
          result.submitBtn.click();
          await sleep(2000);
          status("Submitted ✓ — added to your Mithra tracker.");
          log("Application submitted");
        } else if (didSubmit) {
          status("Couldn't find the submit button — please click it on the page.");
        } else {
          status("Filled. Submit on the page when you're ready.");
        }
        // Report either way (user intends to apply); server records once.
        await bg({ type: "REPORT_APPLICATION", application: {
          job_id: location.href, company: meta.company, role: meta.title,
          job_url: location.href, platform: site, status: "applied" } });
        setBtn("Auto-Fill & Apply", false);
        running = false;
      };
      confirmBox.querySelector(".mithra-submit").onclick = () => finish(true);
      confirmBox.querySelector(".mithra-manual").onclick = () => finish(false);
    } catch (e) {
      status("Something went wrong: " + String(e).slice(0, 80));
      setBtn("Auto-Fill & Apply", false);
      running = false;
    }
  }

  // Mount the panel once the page settles
  function maybeMount() {
    if (document.getElementById("mithra-panel")) return;
    ui();
  }
  setTimeout(maybeMount, 1200);
  // LinkedIn is a SPA — re-mount on navigation
  let lastUrl = location.href;
  setInterval(() => { if (location.href !== lastUrl) { lastUrl = location.href; setTimeout(maybeMount, 1000); } }, 1500);
})();
