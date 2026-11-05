(() => {
  "use strict";
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  async function requestJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
    const data = response.status === 204 ? {} : await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không thể cập nhật kết nối Google.");
    return data;
  }
  function createChatPanel(root, { requestJson: request = requestJson, onBusyChange = () => {} } = {}) {
    if (!root) return null;
    const choose = root.querySelector("[data-google-choose]");
    if (!choose) return null;
    const chips = root.querySelector("[data-google-chips]"), count = root.querySelector("[data-google-count]");
    const status = root.querySelector("[data-google-status]");
    const dialog = el("dialog"); dialog.className = "document-picker"; dialog.setAttribute("aria-label", "Chọn file Google cho hội thoại");
    root.append(dialog);
    let conversation = null, epoch = 0, disabled = false, busy = false, available = [], selected = [];
    const controls = () => { choose.disabled = disabled || busy || !conversation || !available.length; };
    const render = () => {
      chips.replaceChildren();
      for (const file of selected) chips.append(el("span", file.name, "document-chip"));
      count.textContent = `${selected.length} file Google`;
    };
    async function setConversation(id) {
      const current = ++epoch; conversation = id; available = []; selected = [];
      if (dialog.open) dialog.close();
      render(); controls(); status.textContent = "Đang tải file Google…";
      try {
        const state = await request("/api/connections/google");
        const connections = state.connections.filter(item => item.status === "connected");
        const lists = await Promise.all(connections.map(item => request(`/api/connections/google/${item.id}/files`)));
        const chosen = id ? await request(`/api/conversations/${id}/google-files`) : { items: [] };
        if (current !== epoch) return;
        available = lists.flatMap(item => item.items); selected = chosen.items;
        status.textContent = connections.length ? "" : "Kết nối hoặc kết nối lại Google tại trang Connectors.";
        render();
      } catch (error) { if (current === epoch) status.textContent = error.message; }
      finally { if (current === epoch) controls(); }
    }
    async function open() {
      if (choose.disabled) return;
      const current = epoch, context = conversation;
      dialog.replaceChildren();
      const shell = el("div", undefined, "document-picker-shell");
      const header = el("header"); header.append(el("h2", "File Google cho cuộc trò chuyện"));
      const close = el("button", "Đóng", "document-text-button"); close.type = "button";
      close.onclick = () => dialog.close(); header.append(close); shell.append(header);
      const list = el("div", undefined, "google-file-list");
      for (const file of available) {
        const row = el("label", undefined, "google-file-row"), box = el("input"); box.type = "checkbox";
        box.checked = selected.some(item => item.file_id === file.file_id && item.connection_id === file.connection_id);
        box.dataset.fileId = file.file_id; box.dataset.connectionId = file.connection_id;
        row.append(box, el("span", file.name)); list.append(row);
      }
      shell.append(list);
      const footer = el("footer"), link = el("a", "Thêm file tại Connectors"); link.href = "/connectors";
      const save = el("button", "Lưu lựa chọn", "ghost-button"); save.type = "button"; save.dataset.googleSave = "";
      save.onclick = async () => {
        if (busy || current !== epoch || disabled) return;
        busy = true; save.disabled = true; onBusyChange(true); controls();
        try {
          const refs = [...list.querySelectorAll("input:checked")].map(box => ({ connection_id: box.dataset.connectionId, file_id: box.dataset.fileId, context_id: context }));
          const result = await request(`/api/conversations/${context}/google-files`, { method: "PUT", body: JSON.stringify({ file_refs: refs }) });
          if (current !== epoch) return;
          selected = result.items; render(); dialog.close(); status.textContent = "Đã lưu lựa chọn.";
        } catch (error) { if (current === epoch) status.textContent = error.message; }
        finally { busy = false; onBusyChange(false); controls(); if (current === epoch) save.disabled = false; }
      };
      footer.append(link, save); shell.append(footer); dialog.append(shell); dialog.showModal();
    }
    choose.onclick = open;
    return { setConversation, open, setDisabled(value) { disabled = value; controls(); } };
  }
  let pickerLoader;
  function loadPicker() {
    if (window.google?.picker) return Promise.resolve();
    if (!pickerLoader) pickerLoader = new Promise((resolve, reject) => {
      const load = () => window.gapi.load("picker", { callback: resolve, onerror: () => reject(new Error("Không tải được Google Picker.")) });
      if (window.gapi) load();
      else {
        const script = el("script"); script.src = "https://apis.google.com/js/api.js"; script.onload = load;
        script.onerror = () => reject(new Error("Không tải được Google Picker.")); document.head.append(script);
      }
    }).catch(error => { pickerLoader = null; throw error; });
    return pickerLoader;
  }
  async function pickFiles(connectionId, request = requestJson) {
    await loadPicker();
    let config = await request(`/api/connections/google/${connectionId}/picker-config`, { method: "POST" });
    return new Promise((resolve, reject) => {
      let picker, timer, settled = false;
      const finish = (value, error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer); config = null;
        if (picker) { picker.setVisible(false); picker.dispose?.(); picker = null; }
        error ? reject(error) : resolve(value);
      };
      try {
        const view = new google.picker.DocsView().setIncludeFolders(false).setSelectFolderEnabled(false)
          .setMimeTypes("application/vnd.google-apps.spreadsheet,application/vnd.google-apps.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf");
        picker = new google.picker.PickerBuilder().setDeveloperKey(config.api_key).setAppId(config.app_id)
          .setOAuthToken(config.access_token).setOrigin(location.origin).addView(view)
          .enableFeature(google.picker.Feature.MULTISELECT_ENABLED).setCallback(data => {
            if (data.action === google.picker.Action.PICKED) finish(data.docs.map(item => item.id));
            else if (data.action === google.picker.Action.CANCEL) finish([]);
          }).build();
        const ttl = new Date(config.expires_at).getTime() - Date.now();
        timer = setTimeout(() => finish([], new Error("Phiên Picker hết hạn; hãy chọn lại.")), Math.max(1000, ttl));
        config = null; picker.setVisible(true);
      } catch { finish([], new Error("Không thể mở Google Picker.")); }
    });
  }
  async function initPage(root, request = requestJson) {
    const connect = root.querySelector("[data-google-connect]"), pick = root.querySelector("[data-google-pick]");
    const disconnect = root.querySelector("[data-google-disconnect]"), status = root.querySelector("[data-google-status]");
    const account = root.querySelector("[data-google-account]"), files = root.querySelector("[data-google-files]");
    let connection, currentFiles = [], busy = false, epoch = 0, configured = false;
    const controls = () => { connect.disabled = busy || !configured; pick.disabled = busy || connection?.status !== "connected"; disconnect.disabled = busy || !connection || connection.status === "disconnected"; };
    async function refresh() {
      const current = ++epoch; controls();
      const state = await request("/api/connections/google");
      if (current !== epoch) return;
      connection = state.connections[0]; files.replaceChildren();
      configured = state.configured;
      connect.disabled = !state.configured;
      if (!state.configured) status.textContent = "Chưa cấu hình Google OAuth. Xem hướng dẫn Connectors để cấu hình backend.";
      account.textContent = connection ? `${connection.account_email || connection.account_name || "Google"} · ${connection.status}` : "Chưa kết nối Google.";
      connect.textContent = connection?.status === "reconnect" ? "Kết nối lại Google" : "Kết nối Google";
      currentFiles = connection?.status === "connected" ? (await request(`/api/connections/google/${connection.id}/files`)).items : [];
      if (current !== epoch) return;
      if (!currentFiles.length) files.append(el("p", "Chưa chọn file nào."));
      for (const file of currentFiles) {
        const row = el("div", undefined, "google-file-row"); row.append(el("span", file.name));
        const revoke = el("button", "Thu hồi", "document-text-button"); revoke.type = "button";
        revoke.onclick = () => act(async () => { await request(`/api/connections/google/${connection.id}/files/${encodeURIComponent(file.file_id)}`, { method: "DELETE" }); await refresh(); });
        row.append(revoke); files.append(row);
      }
      controls(); if (!state.configured) connect.disabled = true;
    }
    async function act(action) {
      if (busy) return;
      busy = true; status.textContent = "Đang xử lý…"; controls();
      try { await action(); status.textContent = "Đã cập nhật."; }
      catch (error) { status.textContent = error.message; }
      finally { busy = false; controls(); }
    }
    connect.onclick = () => act(async () => { const result = await request("/api/connections/google", { method: "POST" }); location.assign(result.authorization_url); });
    disconnect.onclick = () => act(async () => { await request(`/api/connections/google/${connection.id}`, { method: "DELETE" }); await refresh(); });
    pick.onclick = () => act(async () => {
      const id = connection.id, selected = await pickFiles(id, request);
      if (!selected.length) return;
      const latest = await request(`/api/connections/google/${id}/files`);
      await request(`/api/connections/google/${id}/files`, { method: "PUT", body: JSON.stringify({ file_ids: [...new Set([...latest.items.map(file => file.file_id), ...selected])] }) });
      await refresh();
    });
    await refresh().catch(error => { status.textContent = error.message; controls(); });
    return { refresh };
  }
  window.GoogleConnectors = { createChatPanel, initPage, pickFiles };
  const page = document.querySelector("[data-google-connectors]");
  if (page) initPage(page);
})();
