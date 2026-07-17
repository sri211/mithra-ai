// Runs ONLY on mithraai.in. Reads the logged-in auth token from the web app's
// localStorage and hands it to the extension. This is how the extension "connects"
// to the user's Mithra account without a separate login — BLOCKAGE #1 (auth) solved:
// the token comes from the site the user already trusts and is logged into.
(function () {
  function readResume(userId) {
    // The web app keeps the working resume in "mithra-resume-<userId>" (userStorage).
    // Capturing it here means the extension can fill forms even if the user never
    // clicked "Save" — which was why LinkedIn showed "Filled 0 fields".
    try {
      const keys = [];
      if (userId) keys.push(`mithra-resume-${userId}`);
      keys.push("mithra-resume-guest");
      // Fall back: scan for any mithra-resume-* key
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("mithra-resume-") && !keys.includes(k)) keys.push(k);
      }
      for (const k of keys) {
        const raw = localStorage.getItem(k);
        if (!raw) continue;
        const resume = JSON.parse(raw)?.state?.resume;
        if (resume && (resume.personal?.name || (resume.experience || []).length)) return resume;
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function syncToken() {
    try {
      const raw = localStorage.getItem("mithra-auth");
      if (!raw) return;
      const state = JSON.parse(raw)?.state || {};
      const token = state.accessToken;
      const user = state.user;
      if (token) {
        chrome.runtime.sendMessage({
          type: "MITHRA_TOKEN",
          token,
          user: user ? { name: user.name, email: user.email, plan: user.plan } : null,
          resume: readResume(user?.id),
        });
      }
    } catch (e) { /* ignore */ }
  }
  // Sync on load and whenever the tab regains focus (token may have refreshed)
  syncToken();
  window.addEventListener("focus", syncToken);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) syncToken(); });

  // Let the web app explicitly push a "connect" too (future: a Connect button)
  window.addEventListener("message", (e) => {
    if (e.source === window && e.data?.type === "MITHRA_CONNECT_EXTENSION" && e.data.token) {
      chrome.runtime.sendMessage({ type: "MITHRA_TOKEN", token: e.data.token, user: e.data.user || null });
    }
  });
})();
