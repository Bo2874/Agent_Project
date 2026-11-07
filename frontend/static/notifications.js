(() => {
  const badge = document.querySelector("[data-inbox-count]"), list = document.querySelector("[data-inbox-list]");
  if (!badge && !list) return;
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    if (response.status === 401) { location.assign("/login"); throw new Error("Phiên đăng nhập đã hết hạn."); }
    if (!response.ok) throw new Error("Không thể tải hộp thư.");
    return response.json();
  }
  async function refresh() {
    try {
      const data = await request("/api/notifications");
      if (badge) { badge.hidden = !data.unread_count; badge.textContent = data.unread_count > 99 ? "99+" : data.unread_count; badge.setAttribute("aria-label", `${data.unread_count} thông báo chưa đọc`); }
      if (list) {
        list.replaceChildren();
        if (!data.items.length) { const empty = document.createElement("p"); empty.textContent = "Chưa có thông báo. Kết quả từ Automations sẽ xuất hiện ở đây."; list.append(empty); }
        for (const row of data.items) {
          const card = document.createElement("article"); card.className = "automation-notice"; if (!row.read_at) card.classList.add("unread");
          const link = document.createElement("a"); link.textContent = row.title; link.href = `/automations?automation=${encodeURIComponent(row.result.automation_id)}&run=${encodeURIComponent(row.result.automation_run_id)}`;
          link.addEventListener("click", event => { event.preventDefault(); void request(`/api/notifications/${row.id}/read`, { method: "POST" }).then(() => location.assign(link.href)).catch(error => { document.querySelector("[data-inbox-status]").textContent = error.message; }); });
          const date = document.createElement("small"); date.textContent = new Date(row.created_at).toLocaleString("vi-VN");
          card.append(link, date);
          if (!row.read_at) { const read = document.createElement("button"); read.className = "document-text-button"; read.type = "button"; read.textContent = "Đánh dấu đã đọc"; read.onclick = () => { void request(`/api/notifications/${row.id}/read`, { method: "POST" }).then(refresh); }; card.append(read); }
          list.append(card);
        }
      }
    } catch (error) { const status = document.querySelector("[data-inbox-status]"); if (status) status.textContent = error.message; }
  }
  window.addEventListener("automation-updated", () => { void refresh(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
  setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
  void refresh();
})();
