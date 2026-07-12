// Low-level form toolkit shared by all site adapters. Every hard blockage of
// in-browser form filling is solved here.
(function () {
  const rand = (a, b) => a + Math.random() * (b - a);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function isVisible(el) {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  }

  // BLOCKAGE #9 — React/Vue controlled inputs ignore `el.value = x`. Use the
  // native prototype setter, then dispatch the events the framework listens for.
  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // BLOCKAGE #7 — behave like a human: focus, brief pause, type in small chunks.
  async function fillText(el, value) {
    if (!el || value == null || value === "") return false;
    try {
      el.focus();
      await sleep(rand(40, 120));
      setNativeValue(el, "");
      // type in a few chunks so frameworks re-render naturally
      const chunks = String(value).match(/.{1,6}/g) || [String(value)];
      let acc = "";
      for (const c of chunks) {
        acc += c;
        setNativeValue(el, acc);
        await sleep(rand(15, 45));
      }
      el.dispatchEvent(new Event("blur", { bubbles: true }));
      return true;
    } catch (e) { return false; }
  }

  // BLOCKAGE #6 — content scripts can't assign input.files directly, but a
  // DataTransfer-built FileList IS accepted. Rebuild the PDF from base64.
  function base64ToFile(base64, filename, mime = "application/pdf") {
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], filename, { type: mime });
  }
  async function fillFileInput(input, base64, filename) {
    try {
      const file = base64ToFile(base64, filename);
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(400, 800));
      return true;
    } catch (e) { return false; }
  }

  // The text near a field, used to understand what it's asking for.
  function labelTextFor(el) {
    let t = "";
    try {
      if (el.id) {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) t += " " + lab.innerText;
      }
      const wrapLabel = el.closest("label");
      if (wrapLabel) t += " " + wrapLabel.innerText;
      t += " " + (el.getAttribute("aria-label") || "");
      t += " " + (el.getAttribute("placeholder") || "");
      t += " " + (el.getAttribute("name") || "");
      // parent block text (custom form rows)
      const block = el.closest("[class*='form'], .field, [class*='question'], div");
      if (block) t += " " + (block.innerText || "").slice(0, 80);
    } catch (e) {}
    return t.toLowerCase();
  }

  function matches(text, hints) {
    return hints.some((h) => text.includes(h));
  }

  // Find the best visible input/textarea whose surrounding text matches hints.
  function findField(hints, opts = {}) {
    const selector = opts.selector || "input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea";
    const els = [...document.querySelectorAll(selector)].filter(isVisible);
    for (const el of els) {
      const t = labelTextFor(el);
      if (matches(t, hints) && !(opts.exclude && matches(t, opts.exclude))) return el;
    }
    return null;
  }

  // Native <select>: choose the first option matching any keyword, else first real one.
  async function selectNative(sel, keywords) {
    try {
      if (!isVisible(sel)) return false;
      const opts = [...sel.options];
      let chosen = null;
      for (const kw of keywords.filter(Boolean)) {
        chosen = opts.find((o) => o.text.toLowerCase().includes(String(kw).toLowerCase()) && o.value);
        if (chosen) break;
      }
      if (!chosen) chosen = opts.find((o) => o.value && !/select|choose|please/i.test(o.text));
      if (!chosen) return false;
      sel.value = chosen.value;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(150, 350));
      return true;
    } catch (e) { return false; }
  }

  // Custom (non-native) dropdown: click to open, click the best option.
  async function selectCustom(trigger, keywords) {
    try {
      trigger.scrollIntoView({ block: "center" });
      trigger.click();
      await sleep(rand(350, 600));
      const optSel = "[role=option], li[role=option], [class*='option'], [class*='menu'] li, [class*='dropdown'] li";
      const options = [...document.querySelectorAll(optSel)].filter(isVisible);
      let chosen = null;
      for (const kw of keywords.filter(Boolean)) {
        chosen = options.find((o) => o.innerText.toLowerCase().includes(String(kw).toLowerCase()));
        if (chosen) break;
      }
      if (!chosen) chosen = options[0];
      if (chosen) { chosen.click(); await sleep(rand(200, 400)); return true; }
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return false;
    } catch (e) { return false; }
  }

  function clickByText(texts, root = document) {
    const btns = [...root.querySelectorAll("button, a, [role=button], input[type=submit]")].filter(isVisible);
    for (const t of texts) {
      const b = btns.find((el) => (el.innerText || el.value || "").trim().toLowerCase() === t.toLowerCase())
        || btns.find((el) => (el.innerText || el.value || "").trim().toLowerCase().includes(t.toLowerCase()));
      if (b) { b.scrollIntoView({ block: "center" }); b.click(); return true; }
    }
    return false;
  }

  function hasVisibleCaptcha() {
    const sels = ["iframe[title*='recaptcha' i]", "iframe[src*='recaptcha/api2/bframe']",
                  ".g-recaptcha", ".h-captcha", "iframe[src*='hcaptcha']"];
    return sels.some((s) => {
      const el = document.querySelector(s);
      if (!el || !isVisible(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width > 60 && r.height > 40;
    });
  }

  window.MithraEngine = {
    sleep, rand, isVisible, setNativeValue, fillText, fillFileInput, base64ToFile,
    labelTextFor, matches, findField, selectNative, selectCustom, clickByText, hasVisibleCaptcha,
  };
})();
