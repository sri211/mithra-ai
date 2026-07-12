// Runs ONLY on mithraai.in. Reads the logged-in auth token from the web app's
// localStorage and hands it to the extension. This is how the extension "connects"
// to the user's Mithra account without a separate login — BLOCKAGE #1 (auth) solved:
// the token comes from the site the user already trusts and is logged into.
(function () {
  function syncToken() {
    try {
      const raw = localStorage.getItem("mithra-auth");
      if (!raw) return;
      const token = JSON.parse(raw)?.state?.accessToken;
      const user = JSON.parse(raw)?.state?.user;
      if (token) {
        chrome.runtime.sendMessage({
          type: "MITHRA_TOKEN",
          token,
          user: user ? { name: user.name, email: user.email, plan: user.plan } : null,
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
