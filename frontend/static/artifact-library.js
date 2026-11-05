/* Private working files. Selection pins a version; upload never starts RAG. */
(() => {
  const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const editableFile = item => item.can_edit !== false && [XLSX, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/pdf'].includes(item.mime_type);
  const node = (tag, className = "", text = "") => {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  };
  const action = (text, handler, className = "ghost-button") => {
    const button = node("button", className, text);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  };
  async function requestJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    if (response.status === 401) { window.location.assign("/login"); throw new Error("Phiên đăng nhập đã hết hạn."); }
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không thể xử lý file.");
    return data;
  }
  const sourceLabel = origin => ({ upload: "Bạn tải lên", agent: "Agent tạo", document: "Bản sao từ RAG" })[origin] || "File làm việc";
  const sizeLabel = bytes => `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(bytes / 1024)} KB`;
  function showLibraryTab(name) {
    document.querySelectorAll("[data-library-tab]").forEach(button => {
      const selected = button.dataset.libraryTab === name;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    const working = document.getElementById("working-library");
    const rag = document.getElementById("rag-library");
    if (working) working.hidden = name !== "working";
    if (rag) rag.hidden = name === "working";
    const quota = document.querySelector("[data-rag-quota]");
    if (quota) quota.hidden = name === "working";
  }
  const tabs = [...document.querySelectorAll("[data-library-tab]")];
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => showLibraryTab(button.dataset.libraryTab));
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      showLibraryTab(tabs[next].dataset.libraryTab); tabs[next].focus();
    });
  });
  if (tabs.length) showLibraryTab(new URL(location.href).searchParams.get("tab") === "working" ? "working" : "rag");

  function createLibrary(root) {
    const find = name => root.querySelector(`[data-artifact-${name}]`);
    const status = find("status"), list = find("list"), filters = find("filters"), upload = find("upload");
    const input = upload.querySelector("input[type=file]");
    let busy = false, offset = 0, total = 0, generation = 0, items = [];
    const limit = 20;
    function message(text, error = false) { status.textContent = text; status.classList.toggle("is-error", error); }
    function controls() {
      root.querySelectorAll("input,select,button").forEach(element => { element.disabled = busy; });
      find("prev").disabled = busy || offset === 0;
      find("next").disabled = busy || offset + limit >= total;
    }
    function paint() {
      list.replaceChildren();
      for (const item of items) {
        const row = node("article", "document-card artifact-library-card");
        const description = node("div", "document-description");
        const open = window.ArtifactWorkspace?.onOpen;
        description.append(open ? action(item.name, () => open(item.version_id, "library"), "artifact-name-button") : node("strong", "", item.name));
        description.append(node("p", "document-hint", `${sourceLabel(item.origin)} · v${item.version_number} · ${sizeLabel(item.size_bytes)}`));
        if (!item.can_edit) description.append(node("p", "document-hint", "Bản sao nguồn được giữ nguyên để tải xuống. Định dạng hoặc cấu trúc này chưa hỗ trợ chỉnh sửa."));
        const download = node("a", "ghost-button", "Tải xuống");
        download.href = `/api/artifact-versions/${encodeURIComponent(item.version_id)}/download`;
        const actions = node("div", "document-card-actions"); actions.append(download);
        row.append(description, actions); list.append(row);
      }
      if (!items.length) list.append(node("p", "document-empty", "Chưa có file phù hợp. Tải Excel lên hoặc tạo file bằng yêu cầu trong chat."));
      find("page").textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total}` : "0 file";
      controls();
    }
    async function refresh() {
      const current = ++generation;
      list.setAttribute("aria-busy", "true");
        const params = new URLSearchParams();
        for (const [key, value] of new FormData(filters)) if (value) params.set(key, value);
      params.set("limit", String(limit)); params.set("offset", String(offset));
      try {
        const data = await requestJson(`/api/artifacts?${params}`);
        if (current !== generation) return;
        items = data.items; total = data.total;
        if (offset && offset >= total) { offset = Math.max(0, Math.floor((total - 1) / limit) * limit); return refresh(); }
        find("usage").textContent = `Đã dùng ${sizeLabel(data.usage_bytes)} / ${sizeLabel(data.max_user_bytes)} · bao gồm các phiên bản cũ`;
        paint(); message(`${total} file làm việc`);
      } catch (error) {
        if (current !== generation) return;
        message(error.message, true); list.replaceChildren();
      } finally { if (current === generation) { list.setAttribute("aria-busy", "false"); controls(); } }
    }
    upload.addEventListener("submit", async event => {
      event.preventDefault(); if (busy || !input.files.length) return;
      busy = true; controls();
      try {
        for (const file of input.files) {
          if (!/\.(xlsx|docx|pdf)$/i.test(file.name)) throw new Error("Chọn Excel .xlsx, Word .docx hoặc PDF có văn bản.");
          if (file.size > 20971520) throw new Error("File vượt giới hạn 20 MiB.");
          message(`Đang lưu và kiểm tra ${file.name}…`);
          const body = new FormData(); body.append("file", file);
          await requestJson("/api/artifacts", { method: "POST", body });
        }
        input.value = ""; offset = 0;
        await refresh();
        message("Đã lưu file. Mở chat và chọn File làm việc để chỉnh sửa.");
      } catch (error) { message(error.message, true); }
      finally { busy = false; controls(); }
    });
    filters.addEventListener("submit", event => { event.preventDefault(); offset = 0; void refresh(); });
    find("refresh").addEventListener("click", () => { void refresh(); });
    find("prev").addEventListener("click", () => { offset = Math.max(0, offset - limit); void refresh(); });
    find("next").addEventListener("click", () => { offset += limit; void refresh(); });
    window.addEventListener("artifact-library-changed", () => { void refresh(); });
    window.addEventListener("artifact-preview-available", paint);
    void refresh();
    return { refresh };
  }

  function createChatPanel(root, { onBusyChange = () => {}, requestJson: request = requestJson } = {}) {
    const section = root.querySelector("[data-artifact-selection]");
    if (!section) return null;
    const choose = section.querySelector("[data-working-choose]");
    const count = section.querySelector("[data-working-count]");
    const chips = section.querySelector("[data-working-chips]");
    const status = section.querySelector("[data-working-status]");
    const dialog = node("dialog", "document-picker working-file-picker");
    dialog.setAttribute("aria-label", "Chọn file làm việc cho hội thoại");
    const heading = node("header");
    const close = action("×", () => dialog.close(), "document-text-button"); close.setAttribute("aria-label", "Đóng");
    heading.append(node("h2", "", "Chọn file làm việc"), close);
    const filters = node("form", "document-filters");
    const search = node("input"); search.type = "search"; search.maxLength = 255;
    search.placeholder = "Tìm tên file…"; search.setAttribute("aria-label", "Tìm file làm việc");
    const searchButton = action("Tìm", () => {}); searchButton.type = "submit";
    filters.append(search, searchButton);
    const hint = node("p", "document-hint", "Mỗi lựa chọn giữ đúng phiên bản. File mới được tải lên tại thư viện.");
    const library = node("a", "text-link", "Mở thư viện để tải file"); library.href = "/documents?tab=working";
    const pickerStatus = node("p", "document-status"); pickerStatus.setAttribute("role", "status");
    const list = node("div", "document-picker-list");
    const paging = node("div", "document-pagination");
    const previous = action("Trước", () => { offset = Math.max(0, offset - 20); void loadPicker(); });
    const next = action("Sau", () => { offset += 20; void loadPicker(); });
    const page = node("span"); paging.append(previous, page, next);
    const footer = node("footer");
    const clear = action("Bỏ chọn tất cả", () => { draft.clear(); renderPicker(); });
    const selectedCount = node("span");
    const saveButton = action("Lưu lựa chọn", () => { void save([...draft]); });
    footer.append(clear, selectedCount, saveButton);
    const shell = node("div", "document-picker-shell");
    shell.append(heading, hint, library, filters, pickerStatus, list, paging, footer); dialog.append(shell); section.append(dialog);
    let conversationId = null, generation = 0, pickerGeneration = 0, busy = false, externalDisabled = false;
    let known = false, selectionLoading = false, items = [], candidates = [], draft = new Set(), offset = 0, total = 0;
    function controls() {
      choose.disabled = busy || externalDisabled || !known || !conversationId;
      chips.querySelectorAll("button").forEach(button => { button.disabled = busy || externalDisabled; });
      dialog.querySelectorAll("button,input").forEach(element => { element.disabled = busy || externalDisabled || element.dataset.editable === "false"; });
      previous.disabled = busy || externalDisabled || offset === 0;
      next.disabled = busy || externalDisabled || offset + 20 >= total;
      selectedCount.textContent = `${draft.size} phiên bản được chọn`;
      saveButton.disabled = busy || externalDisabled || draft.size > 20;
    }
    function notify() { controls(); onBusyChange(busy || dialog.open); }
    function render() {
      count.textContent = known ? `${items.length} file được chọn` : "Chưa tải được lựa chọn";
      chips.replaceChildren();
      for (const item of items) {
        const chip = node("div", "document-chip");
        chip.append(node("span", "document-chip-name", item.name));
        const remove = action("×", () => { void save(items.filter(other => other.version_id !== item.version_id).map(other => other.version_id)); }, "document-text-button");
        remove.setAttribute("aria-label", `Bỏ chọn ${item.name}`); chip.append(remove); chips.append(chip);
      }
      controls();
    }
    function renderPicker() {
      list.replaceChildren();
      const merged = new Map([...items, ...candidates].map(item => [item.version_id, item]));
      for (const item of merged.values()) {
        const row = node("label", "document-picker-item");
        const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.value = item.version_id;
        checkbox.checked = draft.has(item.version_id);
        const editable = editableFile(item);
        checkbox.dataset.editable = String(editable);
        checkbox.disabled = busy || externalDisabled || !editable;
        checkbox.addEventListener("change", () => { if (checkbox.checked) draft.add(item.version_id); else draft.delete(item.version_id); controls(); });
        const text = node("span"); text.append(node("strong", "", item.name));
        text.append(node("small", "document-hint", editable ? `v${item.version_number || "đã chọn"} · ${sizeLabel(item.size_bytes)}` : "Định dạng hoặc cấu trúc chưa hỗ trợ chỉnh sửa"));
        row.append(checkbox, text); list.append(row);
      }
      if (!merged.size) list.append(node("p", "document-empty", "Chưa có file phù hợp. Tải Excel, Word hoặc PDF văn bản tại thư viện hoặc yêu cầu Agent tạo file."));
      page.textContent = total ? `${offset + 1}–${Math.min(offset + 20, total)} / ${total}` : "0 file";
      controls();
      list.querySelectorAll("input[type=checkbox]").forEach(input => {
        const item = merged.get(input.value); input.disabled = busy || externalDisabled || !editableFile(item);
      });
    }
    async function loadPicker() {
      const current = ++pickerGeneration, scope = generation;
      busy = true; notify(); pickerStatus.textContent = "Đang tải file…";
      try {
        const data = await request(`/api/artifacts?${new URLSearchParams({ q: search.value, limit: "20", offset: String(offset) })}`);
        if (current !== pickerGeneration || scope !== generation || !dialog.open) return;
        candidates = data.items; total = data.total; pickerStatus.textContent = "Chọn tối đa 20 phiên bản. File vẫn ở thư viện khi bỏ chọn.";
      } catch (error) { if (current === pickerGeneration && scope === generation) pickerStatus.textContent = error.message; }
      finally { if (current === pickerGeneration && scope === generation) { busy = false; renderPicker(); notify(); } }
    }
    async function loadSelection(id, current) {
      const data = await request(`/api/conversations/${encodeURIComponent(id)}/artifacts`);
      if (current !== generation || id !== conversationId) return;
      items = data.items; known = true; render();
    }
    async function save(ids) {
      if (busy || externalDisabled || !conversationId) return;
      const id = conversationId, current = generation; busy = true; notify();
      try {
        const data = await request(`/api/conversations/${encodeURIComponent(id)}/artifacts`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version_ids: ids }),
        });
        if (current !== generation || id !== conversationId) return;
        items = data.items; known = true; render(); dialog.close(); status.textContent = "Đã lưu đúng phiên bản cho hội thoại.";
      } catch (error) {
        if (current !== generation) return;
        (dialog.open ? pickerStatus : status).textContent = error.message;
        try { await loadSelection(id, current); } catch { known = false; render(); }
      } finally { if (current === generation) { busy = false; notify(); } }
    }
    choose.addEventListener("click", () => {
      if (choose.disabled) return;
      draft = new Set(items.map(item => item.version_id)); offset = 0; dialog.showModal(); notify(); void loadPicker();
    });
    dialog.addEventListener("close", () => { ++pickerGeneration; if (!selectionLoading) busy = false; notify(); });
    filters.addEventListener("submit", event => { event.preventDefault(); offset = 0; void loadPicker(); });
    return {
      setDisabled(value) { externalDisabled = value; controls(); },
      async setConversation(id) {
        const current = ++generation; ++pickerGeneration;
        selectionLoading = true;
        if (dialog.open) dialog.close();
        conversationId = id; items = []; known = false; render();
        if (!id) { selectionLoading = false; busy = false; notify(); return; }
        busy = true; notify();
        try { await loadSelection(id, current); if (current === generation) status.textContent = ""; }
        catch (error) { if (current === generation) status.textContent = error.message; }
        finally { if (current === generation) { selectionLoading = false; busy = false; notify(); } }
      },
    };
  }
  window.ArtifactWorkspace = {
    createChatPanel,
    async importDocument(versionId) {
      const result = await requestJson("/api/artifacts/import-document", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_version_id: versionId }),
      });
      window.dispatchEvent(new CustomEvent("artifact-library-changed")); showLibraryTab("working"); return result;
    },
  };
  document.querySelectorAll("[data-artifact-library]").forEach(createLibrary);
})();
