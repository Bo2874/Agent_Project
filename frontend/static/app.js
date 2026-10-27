// Serialize cookie changes across tabs when Web Locks is available.
function withSessionLock(action) {
  return navigator.locks
    ? navigator.locks.request("agent-session-cookie", action)
    : action();
}

const sessionLogoutKey = "agent-session-logout";
function sessionIsLoggingOut() {
  try { return localStorage.getItem(sessionLogoutKey) === "1"; }
  catch { return false; }
}

function setSessionLoggingOut(value) {
  try {
    if (value) localStorage.setItem(sessionLogoutKey, "1");
    else localStorage.removeItem(sessionLogoutKey);
  } catch { /* Session renewal still works when browser storage is unavailable. */ }
}

function createSessionManager() {
  const renewalInterval = 60_000;
  const activityWindow = 90_000;
  let activeUntil = Date.now() + activityWindow;
  let lastRenewed = 0;
  let busy = false;
  let stopped = false;
  let pending = null;
  let timer = null;

  function stop() {
    stopped = true;
    window.clearInterval(timer);
    // Let an in-flight Set-Cookie finish before the logout request clears it.
    return pending || Promise.resolve();
  }

  function renew() {
    if (stopped || sessionIsLoggingOut()) return Promise.resolve();
    if (!busy && (document.hidden || Date.now() > activeUntil)) return Promise.resolve();
    if (pending) return pending;
    if (Date.now() - lastRenewed < renewalInterval) return Promise.resolve();
    pending = withSessionLock(async () => {
      if (stopped || sessionIsLoggingOut()) return;
      if (!busy && (document.hidden || Date.now() > activeUntil)) return;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await fetch("/api/auth/refresh", {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: { "X-Session-Activity": "1" },
          signal: controller.signal,
        });
        if (response.status === 401) {
          stop();
          window.location.assign("/login");
        } else if (response.ok) {
          lastRenewed = Date.now();
        }
      } catch {
        // A temporary network error must not interrupt an AI response. Retry on the next tick.
      } finally {
        window.clearTimeout(timeout);
      }
    }).finally(() => { pending = null; });
    return pending;
  }

  function markActivity(event) {
    if (!event.isTrusted || document.hidden || stopped) return;
    activeUntil = Date.now() + activityWindow;
    void renew();
  }

  ["pointerdown", "keydown", "input", "wheel", "touchmove"].forEach((name) => {
    document.addEventListener(name, markActivity, { passive: true, capture: true });
  });
  document.addEventListener("visibilitychange", markActivity);
  window.addEventListener("focus", markActivity);
  window.addEventListener("online", () => { void renew(); });
  window.addEventListener("storage", (event) => {
    if (event.key === sessionLogoutKey && event.newValue === "1") {
      void stop().finally(() => window.location.assign("/login"));
    }
  });
  window.addEventListener("pagehide", () => window.clearInterval(timer));
  function start() {
    if (stopped) return;
    window.clearInterval(timer);
    timer = window.setInterval(() => { void renew(); }, 15_000);
    void renew();
  }
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      activeUntil = Date.now() + activityWindow;
      start();
    }
  });
  start();
  return {
    stop,
    setBusy(value) {
      busy = value;
      activeUntil = Date.now() + activityWindow;
      void renew();
    },
  };
}

if (document.body.dataset.authenticated === "true") {
  window.agentSession = createSessionManager();
}

function readPayload(form) {
  const data = new FormData(form);
  return Object.fromEntries(
    [...data.entries()].filter(([, value]) => String(value).trim() !== "")
  );
}

function validateAuthForm(form, payload) {
  const emailInput = form.querySelector("input[name='email']");
  const passwordInput = form.querySelector("input[name='password']");

  if (!payload.email) return "Vui lòng nhập email.";
  if (!emailInput.validity.valid) {
    return "Email không đúng định dạng. Ví dụ: ten@example.com.";
  }
  if (!payload.password) return "Vui lòng nhập mật khẩu.";
  if (form.dataset.endpoint.endsWith("/register") && payload.password.length < 8) {
    return "Mật khẩu phải có ít nhất 8 ký tự.";
  }
  if (passwordInput && payload.password.length > 128) {
    return "Mật khẩu không được vượt quá 128 ký tự.";
  }
  return null;
}

document.querySelectorAll("[data-auth-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type='submit']");
    const errorBox = form.querySelector("[data-form-error]");
    const originalText = button.innerHTML;
    const payload = readPayload(form);
    const validationError = validateAuthForm(form, payload);

    errorBox.textContent = "";
    if (validationError) {
      errorBox.textContent = validationError;
      return;
    }
    button.disabled = true;
    button.textContent = "Đang xử lý…";

    try {
      const response = await fetch(form.dataset.endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = response.status === 204 ? {} : await response.json();
      if (!response.ok) {
        const detail = result.detail;
        const message = Array.isArray(detail)
          ? detail.map((item) => item.msg).join(". ")
          : detail || "Không thể xử lý yêu cầu.";
        throw new Error(message);
      }
      setSessionLoggingOut(false);
      window.location.assign(form.dataset.next);
    } catch (error) {
      errorBox.textContent = error.message || "Đã xảy ra lỗi. Vui lòng thử lại.";
      button.disabled = false;
      button.innerHTML = originalText;
    }
  });
});

document.querySelectorAll("[data-logout]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    setSessionLoggingOut(true);
    try {
      await window.agentSession?.stop();
      await withSessionLock(() => fetch("/api/auth/logout", {
        method: "POST", credentials: "same-origin",
      }));
    } finally {
      window.location.assign("/login");
    }
  });
});
