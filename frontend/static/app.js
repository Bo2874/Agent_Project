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
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      window.location.assign("/login");
    }
  });
});
