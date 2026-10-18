const memoryApp = document.querySelector("[data-memory-app]");

if (memoryApp) {
  const list = memoryApp.querySelector("[data-memory-list]");
  const count = memoryApp.querySelector("[data-memory-count]");
  const status = memoryApp.querySelector("[data-memory-status]");
  const filters = memoryApp.querySelector("[data-memory-filters]");
  let memories = [];
  let activeFilter = "all";

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 401) {
      window.location.assign("/login");
      throw new Error("Phiên đăng nhập đã hết hạn.");
    }
    if (response.status === 204) return null;
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể xử lý memory.");
    return result;
  }

  function typeLabel(value) {
    return {
      fact: "Fact",
      preference: "Preference",
      goal: "Goal",
      constraint: "Constraint",
    }[value] || value;
  }

  function sourceLabel(value) {
    return {
      user_message: "Từ hội thoại",
      tool_result: "Từ tool",
      manual: "Bạn đã chỉnh sửa",
      import: "Dữ liệu import",
    }[value] || value;
  }

  function sensitivityLabel(value) {
    return {
      normal: "Thông thường",
      personal: "Cá nhân",
      sensitive: "Nhạy cảm",
    }[value] || value;
  }

  function formatDate(value) {
    return new Intl.DateTimeFormat("vi-VN", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  }

  function filteredMemories() {
    if (activeFilter === "all") return memories;
    if (activeFilter === "pinned") return memories.filter((item) => item.is_pinned);
    return memories.filter((item) => item.memory_type === activeFilter);
  }

  function setStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle("is-error", isError);
  }

  function beginEdit(card, memory) {
    if (card.classList.contains("is-editing")) return;
    card.classList.add("is-editing");
    const content = card.querySelector(".memory-content");
    const actions = card.querySelector(".memory-actions");
    const form = document.createElement("form");
    form.className = "memory-edit-form";
    const textarea = document.createElement("textarea");
    textarea.maxLength = 500;
    textarea.required = true;
    textarea.value = memory.content;
    const controls = document.createElement("div");
    controls.className = "memory-edit-controls";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "memory-secondary-button";
    cancel.textContent = "Hủy";
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "memory-save-button";
    save.textContent = "Lưu thay đổi";
    controls.append(cancel, save);
    form.append(textarea, controls);
    content.hidden = true;
    actions.hidden = true;
    content.after(form);
    textarea.focus();

    function close() {
      form.remove();
      content.hidden = false;
      actions.hidden = false;
      card.classList.remove("is-editing");
    }

    cancel.addEventListener("click", close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const nextContent = textarea.value.trim();
      if (!nextContent) return;
      save.disabled = true;
      cancel.disabled = true;
      setStatus("Đang lưu thay đổi…");
      try {
        await requestJson(`/api/memories/${memory.id}`, {
          method: "PATCH",
          body: JSON.stringify({ content: nextContent }),
        });
        await loadMemories("Đã cập nhật memory.");
      } catch (error) {
        setStatus(error.message, true);
        save.disabled = false;
        cancel.disabled = false;
      }
    });
  }

  function createMemoryCard(memory) {
    const card = document.createElement("article");
    card.className = `memory-card${memory.is_pinned ? " is-pinned" : ""}`;
    card.dataset.id = memory.id;

    const top = document.createElement("div");
    top.className = "memory-card-top";
    const badge = document.createElement("span");
    badge.className = `memory-type memory-type-${memory.memory_type}`;
    badge.textContent = typeLabel(memory.memory_type);
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "memory-pin-button";
    pin.setAttribute("aria-pressed", String(memory.is_pinned));
    pin.setAttribute("aria-label", memory.is_pinned ? "Bỏ ghim memory" : "Ghim memory");
    pin.title = memory.is_pinned ? "Bỏ ghim" : "Ghim";
    pin.textContent = memory.is_pinned ? "★" : "☆";
    top.append(badge, pin);

    const key = document.createElement("code");
    key.className = "memory-key";
    key.textContent = memory.memory_key;
    const content = document.createElement("p");
    content.className = "memory-content";
    content.textContent = memory.content;

    const metadata = document.createElement("div");
    metadata.className = "memory-metadata";
    const confidence = document.createElement("span");
    confidence.textContent = `Tin cậy ${Math.round(Number(memory.confidence) * 100)}%`;
    const source = document.createElement("span");
    source.textContent = sourceLabel(memory.source_type);
    const sensitivity = document.createElement("span");
    sensitivity.className = `memory-sensitivity memory-sensitivity-${memory.sensitivity}`;
    sensitivity.textContent = sensitivityLabel(memory.sensitivity);
    const retention = document.createElement("span");
    retention.textContent = memory.is_pinned
      ? "Được giữ khi còn ghim"
      : `Lưu đến ${formatDate(memory.retention_until)}`;
    const observed = document.createElement("time");
    observed.dateTime = memory.observed_at;
    observed.textContent = formatDate(memory.observed_at);
    metadata.append(confidence, source, sensitivity, retention, observed);

    const actions = document.createElement("div");
    actions.className = "memory-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "memory-secondary-button";
    edit.textContent = "Sửa";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "memory-delete-button";
    remove.textContent = "Xóa";
    actions.append(edit, remove);

    pin.addEventListener("click", async () => {
      pin.disabled = true;
      setStatus(memory.is_pinned ? "Đang bỏ ghim…" : "Đang ghim memory…");
      try {
        await requestJson(`/api/memories/${memory.id}/pin`, {
          method: "PATCH",
          body: JSON.stringify({ is_pinned: !memory.is_pinned }),
        });
        await loadMemories(memory.is_pinned ? "Đã bỏ ghim." : "Đã ghim memory.");
      } catch (error) {
        setStatus(error.message, true);
        pin.disabled = false;
      }
    });
    edit.addEventListener("click", () => beginEdit(card, memory));
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Xóa memory “${memory.content}”?`)) return;
      remove.disabled = true;
      setStatus("Đang xóa memory…");
      try {
        await requestJson(`/api/memories/${memory.id}`, { method: "DELETE" });
        await loadMemories("Đã xóa memory.");
      } catch (error) {
        setStatus(error.message, true);
        remove.disabled = false;
      }
    });

    card.append(top, key, content, metadata, actions);
    return card;
  }

  function renderMemories() {
    const visible = filteredMemories();
    list.replaceChildren();
    count.textContent = `${memories.length} memory`;
    if (visible.length === 0) {
      const empty = document.createElement("div");
      empty.className = "memory-empty";
      const heading = document.createElement("h2");
      heading.textContent = memories.length ? "Không có memory phù hợp" : "Chưa có memory nào";
      const description = document.createElement("p");
      description.textContent = memories.length
        ? "Hãy chọn bộ lọc khác."
        : "Hãy trò chuyện với Agent để bắt đầu tạo bộ nhớ cá nhân.";
      empty.append(heading, description);
      list.append(empty);
      return;
    }
    visible.forEach((memory) => list.append(createMemoryCard(memory)));
  }

  async function loadMemories(successMessage = "") {
    try {
      memories = await requestJson("/api/memories");
      renderMemories();
      setStatus(successMessage || "Bộ nhớ đã được đồng bộ.");
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  filters.addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    activeFilter = button.dataset.filter;
    filters.querySelectorAll("[data-filter]").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
    renderMemories();
  });

  loadMemories();
}
