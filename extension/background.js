// Service worker: token storage + all Mithra API calls (kept off the content
// script so the page can never read the token). BLOCKAGE #12 (MV3 no persistent
// background) handled via message passing; BLOCKAGE #15 (token expiry) via ping.
const API = "https://api.mithraai.in/api";

async function getToken() {
  const { mithraToken } = await chrome.storage.local.get("mithraToken");
  return mithraToken || "";
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
        await chrome.storage.local.set({ mithraToken: msg.token, mithraUser: msg.user || null });
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
        if (!res.ok) { sendResponse({ ok: false, status: res.status }); return; }
        sendResponse({ ok: true, profile: await res.json() });
        return;
      }

      if (msg.type === "GET_RESUME_PDF") {
        // Fetch the resume PDF and return it as a base64 data URL so the content
        // script can rebuild a File for upload (BLOCKAGE #6, file upload).
        const token = await getToken();
        const res = await fetch(`${API}/extension/resume.pdf`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
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
