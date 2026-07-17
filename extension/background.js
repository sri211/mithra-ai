// Service worker: token storage + all Mithra API calls (kept off the content
// script so the page can never read the token). BLOCKAGE #12 (MV3 no persistent
// background) handled via message passing; BLOCKAGE #15 (token expiry) via ping.
const API = "https://api.mithraai.in/api";

async function getToken() {
  const { mithraToken } = await chrome.storage.local.get("mithraToken");
  return mithraToken || "";
}

// Build the flat profile the content script fills from, out of a resume JSON.
function buildProfileFromResume(r) {
  const p = r.personal || {};
  const name = p.name || "";
  const parts = name.split(" ");
  const sk = r.skills || {};
  const skills = Array.isArray(sk) ? sk : [...(sk.technical || []), ...(sk.soft || [])];
  let years = 0;
  for (const e of (r.experience || [])) {
    const s = parseInt(String(e.start || "").match(/\d{4}/)?.[0] || "0");
    const en = e.current ? 2026 : parseInt(String(e.end || "").match(/\d{4}/)?.[0] || "0");
    if (s && en) years += Math.max(0, en - s);
  }
  return {
    name,
    first_name: parts[0] || "",
    last_name: parts.slice(1).join(" ") || "",
    email: p.email || "",
    phone: p.phone || "",
    location: p.location || "",
    city: (p.location || "").split(",")[0].trim(),
    linkedin: p.linkedin || "",
    github: p.github || "",
    website: p.website || "",
    headline: p.title || "",
    summary: r.summary || "",
    skills: skills.slice(0, 30),
    years_experience: years,
    has_resume: !!(p.name || (r.experience || []).length),
    answers: {
      notice_period: "30 days",
      willing_to_relocate: "Yes",
      authorized_to_work: "Yes",
      expected_ctc: "",
      current_ctc: "",
      why_this_role: (r.summary || "").slice(0, 280),
    },
  };
}

async function apiFetch(path, opts = {}) {
  const token = await getToken();
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  });
  return res;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "MITHRA_TOKEN") {
        const patch = { mithraToken: msg.token, mithraUser: msg.user || null };
        if (msg.resume) patch.mithraResume = msg.resume;  // captured working resume
        await chrome.storage.local.set(patch);
        sendResponse({ ok: true });
        return;
      }

      if (msg.type === "GET_STATUS") {
        const token = await getToken();
        if (!token) { sendResponse({ connected: false }); return; }
        const res = await apiFetch("/extension/ping");
        if (res.ok) {
          const data = await res.json();
          await chrome.storage.local.set({ mithraUser: { name: data.name, email: data.email, plan: data.plan } });
          sendResponse({ connected: true, user: data });
        } else {
          sendResponse({ connected: false, expired: res.status === 401 });
        }
        return;
      }

      if (msg.type === "GET_PROFILE") {
        const res = await apiFetch("/extension/profile");
        let profile = res.ok ? await res.json() : {};
        // If the saved-resume profile is empty, build one from the working resume
        // captured off the web app (covers users who never clicked "Save").
        const { mithraResume } = await chrome.storage.local.get("mithraResume");
        if ((!profile || !profile.has_resume) && mithraResume) {
          profile = { ...buildProfileFromResume(mithraResume), ...(profile || {}) };
          profile.has_resume = true;
          profile._fromLocal = true;
        }
        if (!profile || (!profile.name && !profile.email)) {
          sendResponse({ ok: false, empty: true });
          return;
        }
        sendResponse({ ok: true, profile });
        return;
      }

      if (msg.type === "GET_RESUME_PDF") {
        // Prefer a PDF built from the captured working resume; fall back to the
        // saved-resume PDF. Returned base64 so the content script rebuilds a File.
        const token = await getToken();
        const { mithraResume } = await chrome.storage.local.get("mithraResume");
        let res;
        if (mithraResume) {
          res = await fetch(`${API}/extension/resume-pdf-from-json`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify({ resume: mithraResume }),
          });
        }
        if (!res || !res.ok) {
          res = await fetch(`${API}/extension/resume.pdf`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
        }
        if (!res.ok) { sendResponse({ ok: false }); return; }
        const buf = await res.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        sendResponse({ ok: true, base64: btoa(binary), filename: "resume.pdf" });
        return;
      }

      if (msg.type === "REPORT_APPLICATION") {
        const res = await apiFetch("/extension/applications", {
          method: "POST",
          body: JSON.stringify(msg.application),
        });
        const data = await res.json().catch(() => ({}));
        sendResponse({ ok: res.ok, status: res.status, data });
        return;
      }

      if (msg.type === "OPEN_CONNECT") {
        chrome.tabs.create({ url: "https://mithraai.in/dashboard" });
        sendResponse({ ok: true });
        return;
      }

      sendResponse({ ok: false, error: "unknown message" });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // async response
});
