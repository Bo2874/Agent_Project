(() => {
  "use strict";

  const labels = {
    uploaded: "Đã lưu, chưa sẵn sàng hỏi đáp", queued: "Đang chờ xử lý",
    parsing: "Đang đọc nội dung", chunking: "Đang chia nội dung",
    embedding: "Đang xử lý", indexing: "Đang hoàn tất", ready: "Sẵn sàng",
    failed: "Lỗi xử lý", cancelled: "Đã hủy xử lý", deleting: "Đang xóa",
  };
  const processing = new Set(["queued", "parsing", "chunking", "embedding", "indexing", "deleting"]);
  const limit = 20;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function button(text, action, className = "ghost-button") {
    const element = node("button", className, text);
    element.type = "button";
    element.addEventListener("click", action);
    return element;
  }

  function bytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${(value / 1024 / 1024).toFixed(1)} MiB`;
  }

  function message(element, text, error = false) {
    element.textContent = text;
    element.classList.toggle("is-error", error);
  }

  function errorDetail(payload) {
    return typeof payload?.detail === "string" ? payload.detail : "Không thể xử lý yêu cầu. Hãy làm mới và thử lại.";
  }

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {
        credentials: "same-origin", cache: "no-store", ...options, signal: controller.signal,
        headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers,
      });
      if (response.status === 401) {
        window.location.assign("/login");
        throw new Error("Phiên đăng nhập đã hết hạn.");
      }
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorDetail(payload));
      if (!payload) throw new Error("Không đọc được phản hồi. Hãy làm mới trước khi thử lại.");
      return payload;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) {
        throw new Error("Chưa nhận được phản hồi. Hãy làm mới trước khi thử lại.");
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function listUrl(form, offset, includeDeleting = false) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    // Disabled filters still apply while an upload/delete refreshes the list.
    for (const element of form.elements) {
      if (element.name && element.value.trim()) params.set(element.name, element.value.trim());
    }
    if (includeDeleting) params.set("include_deleting", "true");
    return `/api/documents?${params}`;
  }

  function upload(file, conversationId, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents");
      xhr.withCredentials = true;
      xhr.timeout = 180000;
      xhr.upload.onprogress = (event) => {
        onProgress(event.lengthComputable ? Math.round(event.loaded * 100 / event.total) : null);
      };
      const uncertain = () => reject(new Error("Chưa nhận được kết quả lưu file. Hãy làm mới thư viện trước khi tải lại."));
      xhr.onerror = uncertain;
      xhr.ontimeout = uncertain;
      xhr.onabort = uncertain;
      xhr.onload = () => {
        if (xhr.status === 401) {
          window.location.assign("/login");
          reject(new Error("Phiên đăng nhập đã hết hạn."));
          return;
        }
        let payload;
        try { payload = JSON.parse(xhr.responseText); } catch { uncertain(); return; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
        else reject(new Error(errorDetail(payload)));
      };
      const data = new FormData();
      data.append("file", file);
      if (conversationId) data.append("conversation_id", conversationId);
      xhr.send(data);
    });
  }

  async function uploadFiles(files, conversationId, progress, status) {
    const usage = await request("/api/documents/usage");
    let saved = 0;
    let reused = 0;
    const errors = [];
    progress.hidden = false;
    try {
      for (const [index, file] of Array.from(files).entries()) {
        progress.value = 0;
        try {
          if (!/\.(pdf|docx?|txt)$/i.test(file.name)) throw new Error("Chỉ hỗ trợ PDF, DOC, DOCX và TXT.");
          if (!file.size) throw new Error("File không có nội dung.");
          if (file.size > usage.max_file_bytes) throw new Error(`Tối đa ${bytes(usage.max_file_bytes)}/file.`);
          const result = await upload(file, conversationId, (percent) => {
            if (percent === null) progress.removeAttribute("value");
            else progress.value = percent;
            message(status, `${index + 1}/${files.length} · ${file.name} · ${percent === 100 ? "Đang kiểm tra và lưu…" : "Đang tải…"}`);
          });
          if (result.deduplicated) reused += 1;
          else saved += 1;
        } catch (error) {
          errors.push(`${file.name}: ${error.message}`);
        }
      }
    } finally {
      progress.hidden = true;
    }
    const summary = `Đã lưu ${saved} file mới${reused ? `, dùng lại ${reused} file có sẵn` : ""}.`;
    message(status, `${summary} ${errors.length ? errors.join("; ") : "File chưa sẵn sàng hỏi đáp."}`, errors.length > 0);
  }

  function documentDescription(item) {
    const info = node("div", "document-info");
    const title = node("strong", "document-name", item.original_filename);
    title.title = item.original_filename;
    const date = new Date(item.created_at).toLocaleDateString("vi-VN");
    info.append(title, node("span", "document-meta", `${item.file_type.toUpperCase()} · ${bytes(item.file_size_bytes)} · ${date}`));
    info.append(node("span", `document-state status-${item.status}`, labels[item.status] || item.status));
    if (item.error_message) info.append(node("span", "document-error", item.error_message));
    return info;
  }

  function createLibraryPage(root) {
    const find = (name) => root.querySelector(`[data-document-${name}]`);
    const form = find("upload-form");
    const input = form.querySelector("input[type=file]");
    const filters = find("filters");
    const list = find("list");
    const status = find("list-status");
    const previous = find("prev");
    const next = find("next");
    let offset = 0;
    let total = 0;
    let busy = false;
    let generation = 0;
    let items = [];

    function controls() {
      form.querySelectorAll("button, input").forEach((element) => { element.disabled = busy; });
      filters.querySelectorAll("button, input, select").forEach((element) => { element.disabled = busy; });
      list.querySelectorAll("button").forEach((element) => { element.disabled = busy; });
      previous.disabled = busy || offset === 0;
      next.disabled = busy || offset + limit >= total;
    }

    async function remove(item) {
      if (busy || !window.confirm(`Xóa “${item.original_filename}” khỏi thư viện và tất cả hội thoại?`)) return;
      busy = true;
      controls();
      try {
        await request(`/api/documents/${item.id}`, { method: "DELETE" });
        message(find("upload-status"), "Đã ngừng truy cập tài liệu. Hệ thống đang dọn file và giải phóng dung lượng.");
      } catch (error) {
        message(find("upload-status"), error.message, true);
      } finally {
        await refresh();
        busy = false;
        controls();
      }
    }

    async function refresh() {
      const current = ++generation;
      list.setAttribute("aria-busy", "true");
      message(status, "Đang tải thư viện…");
      const results = await Promise.allSettled([
        request(listUrl(filters, offset, true)), request("/api/documents/usage"),
      ]);
      if (current !== generation) return;
      const [documents, usage] = results;
      if (usage.status === "fulfilled") {
        const data = usage.value;
        find("usage").textContent = `${bytes(data.used_bytes)} / ${bytes(data.max_user_bytes)} · ${data.document_count}/${data.max_documents} tài liệu · tối đa ${bytes(data.max_file_bytes)}/file`;
        find("quota").max = data.max_user_bytes;
        find("quota").value = data.used_bytes;
      } else {
        find("usage").textContent = "Không tải được thông tin dung lượng.";
      }
      list.setAttribute("aria-busy", "false");
      if (documents.status === "rejected") {
        items = [];
        total = 0;
        list.replaceChildren();
        message(status, documents.reason.message, true);
      } else {
        total = documents.value.total;
        if (!documents.value.items.length && offset > 0) {
          offset = Math.max(0, Math.ceil(total / limit - 1) * limit);
          return refresh();
        }
        items = documents.value.items;
        list.replaceChildren();
        for (const item of items) {
          const row = node("article", "document-card");
          const actions = node("div", "document-card-actions");
          if (item.status !== "deleting") {
            const download = node("a", "ghost-button", "Tải xuống");
            download.href = `/api/documents/${item.id}/download`;
            actions.append(download);
          }
          const removeButton = button(item.status === "deleting" ? "Yêu cầu xóa lại" : "Xóa", () => remove(item), "document-text-button document-delete");
          removeButton.setAttribute("aria-label", `Xóa tài liệu ${item.original_filename}`);
          actions.append(removeButton);
          row.append(documentDescription(item), actions);
          list.append(row);
        }
        if (!items.length) list.append(node("p", "document-empty", "Chưa có tài liệu phù hợp. Tải file lên hoặc đổi bộ lọc."));
        message(status, `${total} tài liệu${items.some((item) => item.status === "deleting") ? " · File đang xóa vẫn tính dung lượng cho đến khi dọn xong." : ""}`);
      }
      find("page-info").textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total}` : "0 tài liệu";
      controls();
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || !input.files.length) return;
      const files = Array.from(input.files);
      busy = true;
      controls();
      try {
        await uploadFiles(files, null, find("upload-progress"), find("upload-status"));
        input.value = "";
        offset = 0;
      } catch (error) {
        message(find("upload-status"), error.message, true);
      } finally {
        await refresh();
        busy = false;
        controls();
      }
    });
    filters.addEventListener("submit", (event) => { event.preventDefault(); if (!busy) { offset = 0; void refresh(); } });
    find("refresh").addEventListener("click", () => { if (!busy) void refresh(); });
    previous.addEventListener("click", () => { if (!previous.disabled) { offset = Math.max(0, offset - limit); void refresh(); } });
    next.addEventListener("click", () => { if (!next.disabled) { offset += limit; void refresh(); } });
    const timer = window.setInterval(() => {
      if (!document.hidden && !busy && list.getAttribute("aria-busy") !== "true"
          && items.some((item) => processing.has(item.status))) void refresh();
    }, 5000);
    window.addEventListener("pagehide", () => window.clearInterval(timer), { once: true });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && !busy) void refresh(); });
    void refresh();
  }

  function createChatPanel(root, { onBusyChange }) {
    const panel = root.querySelector("[data-chat-documents]");
    const find = (name) => panel.querySelector(`[data-chat-document-${name}]`);
    const picker = (name) => panel.querySelector(`[data-picker-${name}]`);
    const dialog = panel.querySelector("[data-document-picker]");
    const filters = picker("filters");
    const fileInput = find("files");
    const status = find("status");
    let conversationId = null;
    let items = [];
    let scopeKnown = false;
    let busy = false;
    let externalDisabled = false;
    let generation = 0;
    let draft = new Set();
    let offset = 0;
    let total = 0;
    let pickerLoading = false;
    let pickerGeneration = 0;

    function controls() {
      const disabled = externalDisabled || busy || !conversationId || dialog.open;
      find("upload").disabled = disabled;
      find("choose").disabled = disabled;
      find("refresh").disabled = disabled;
      fileInput.disabled = disabled;
      find("chips").querySelectorAll("button").forEach((element) => { element.disabled = disabled || !scopeKnown; });
      const locked = busy || externalDisabled;
      dialog.querySelectorAll("button, input, select").forEach((element) => { element.disabled = locked; });
      picker("save").disabled = locked || pickerLoading;
      picker("prev").disabled = locked || pickerLoading || offset === 0;
      picker("next").disabled = locked || pickerLoading || offset + limit >= total;
      picker("count").textContent = `Đã chọn ${draft.size} file`;
    }

    function notify() {
      controls();
      onBusyChange(busy || dialog.open);
    }

    function render() {
      find("chips").replaceChildren();
      find("count").textContent = scopeKnown ? `${items.length} file được chọn` : "Chưa tải được lựa chọn";
      for (const item of items) {
        const chip = node("div", `document-chip status-${item.status}`);
        chip.title = `${item.original_filename} · ${labels[item.status] || item.status}`;
        const name = node("span", "document-chip-name", item.original_filename);
        const remove = button("×", () => saveSelection(items.filter((other) => other.id !== item.id).map((other) => other.id)), "document-text-button");
        remove.setAttribute("aria-label", `Bỏ chọn ${item.original_filename} khỏi hội thoại`);
        chip.append(name, node("small", "", labels[item.status] || item.status), remove);
        find("chips").append(chip);
      }
      controls();
    }

    async function loadScope(id, current, preserveStatus = false) {
      try {
        const data = await request(`/api/conversations/${id}/documents`);
        if (current !== generation || id !== conversationId) return false;
        items = data.items;
        scopeKnown = true;
        render();
        if (!preserveStatus) message(status, items.length ? "File đã lưu. Các file chưa sẵn sàng sẽ chưa dùng để hỏi đáp." : "Chưa chọn tài liệu. Tải file hoặc chọn lại từ thư viện của bạn.");
        return true;
      } catch (error) {
        if (current !== generation || id !== conversationId) return false;
        scopeKnown = false;
        items = [];
        render();
        message(status, error.message, true);
        return false;
      }
    }

    async function refreshScope() {
      if (busy || externalDisabled || !conversationId || dialog.open) return;
      busy = true;
      notify();
      try { await loadScope(conversationId, generation); }
      finally { busy = false; notify(); }
    }

    async function saveSelection(ids) {
      if (busy || externalDisabled || !conversationId) return;
      const id = conversationId;
      busy = true;
      notify();
      try {
        const data = await request(`/api/conversations/${id}/documents`, {
          method: "PUT", body: JSON.stringify({ document_ids: ids }),
        });
        items = data.items;
        scopeKnown = true;
        render();
        if (dialog.open) dialog.close();
        message(status, items.length ? "Đã lưu lựa chọn. File chưa xử lý xong sẽ chưa thể hỏi đáp." : "Đã bỏ chọn tài liệu; file vẫn được giữ trong thư viện.");
      } catch (error) {
        message(dialog.open ? picker("status") : status, error.message, true);
        // A lost response can follow a committed mutation; refresh instead of resending it.
        await loadScope(id, generation, true);
      } finally {
        busy = false;
        notify();
      }
    }

    async function loadPicker() {
      const current = ++pickerGeneration;
      pickerLoading = true;
      picker("list").setAttribute("aria-busy", "true");
      message(picker("status"), "Đang tải thư viện…");
      controls();
      try {
        const data = await request(listUrl(filters, offset));
        if (current !== pickerGeneration || !dialog.open) return;
        total = data.total;
        if (!data.items.length && offset > 0) {
          offset = Math.max(0, Math.ceil(total / limit - 1) * limit);
          return await loadPicker();
        }
        picker("list").replaceChildren();
        for (const item of data.items) {
          const row = node("label", "document-picker-row");
          const checkbox = node("input");
          checkbox.type = "checkbox";
          checkbox.value = item.id;
          checkbox.checked = draft.has(item.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) draft.add(item.id);
            else draft.delete(item.id);
            controls();
          });
          row.append(checkbox, documentDescription(item));
          picker("list").append(row);
        }
        if (!data.items.length) picker("list").append(node("p", "document-empty", "Chưa có tài liệu phù hợp trong thư viện."));
        picker("page").textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total}` : "0 tài liệu";
        message(picker("status"), "Lựa chọn được giữ khi đổi trang hoặc bộ lọc. Bấm Lưu lựa chọn để áp dụng.");
      } catch (error) {
        if (current !== pickerGeneration) return;
        picker("list").replaceChildren();
        picker("page").textContent = "";
        total = 0;
        message(picker("status"), error.message, true);
      } finally {
        if (current === pickerGeneration) {
          pickerLoading = false;
          picker("list").setAttribute("aria-busy", "false");
          controls();
        }
      }
    }

    find("choose").addEventListener("click", async () => {
      if (busy || externalDisabled || !conversationId || dialog.open) return;
      busy = true;
      notify();
      try {
        if (!await loadScope(conversationId, generation)) return;
        draft = new Set(items.map((item) => item.id));
        offset = 0;
        filters.reset();
        dialog.showModal();
        await loadPicker();
      } finally { busy = false; notify(); }
    });
    find("upload").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      if (!fileInput.files.length || busy || externalDisabled || !conversationId) return;
      const files = Array.from(fileInput.files);
      const id = conversationId;
      busy = true;
      notify();
      try { await uploadFiles(files, id, find("progress"), status); }
      catch (error) { message(status, error.message, true); }
      finally {
        fileInput.value = "";
        await loadScope(id, generation, true);
        busy = false;
        notify();
      }
    });
    find("refresh").addEventListener("click", () => { void refreshScope(); });
    picker("close").addEventListener("click", () => { if (!busy) dialog.close(); });
    dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => { ++pickerGeneration; pickerLoading = false; notify(); });
    picker("save").addEventListener("click", () => { void saveSelection(Array.from(draft)); });
    picker("clear").addEventListener("click", () => {
      draft.clear();
      picker("list").querySelectorAll("input[type=checkbox]").forEach((element) => { element.checked = false; });
      controls();
    });
    filters.addEventListener("submit", (event) => { event.preventDefault(); if (!busy) { offset = 0; void loadPicker(); } });
    picker("prev").addEventListener("click", () => { if (!picker("prev").disabled) { offset = Math.max(0, offset - limit); void loadPicker(); } });
    picker("next").addEventListener("click", () => { if (!picker("next").disabled) { offset += limit; void loadPicker(); } });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) void refreshScope(); });
    controls();
    return {
      setDisabled(value) { externalDisabled = value; controls(); },
      async setConversation(id) {
        ++generation;
        conversationId = id;
        items = [];
        scopeKnown = false;
        render();
        if (!id) return;
        busy = true;
        notify();
        try { await loadScope(id, generation); }
        finally { busy = false; notify(); }
      },
    };
  }

  window.DocumentLibrary = { createChatPanel };
  const libraryPage = document.querySelector("[data-documents-page]");
  if (libraryPage) createLibraryPage(libraryPage);
})();
