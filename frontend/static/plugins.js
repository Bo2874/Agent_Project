/* Plugin UI uses only the existing owner/admin control plane; never starts a connector. */
(() => {
  const root = document.querySelector("[data-plugins-app]");
  if (!root) return;
  const status = root.querySelector("[data-plugin-status]");
  const retry = root.querySelector("[data-plugin-retry]");
  const dialog = root.querySelector("[data-plugin-dialog]");
  const form = dialog.querySelector("form");
  const content = dialog.querySelector("[data-plugin-dialog-content]");
  const dialogStatus = dialog.querySelector("[data-plugin-dialog-status]");
  const submit = dialog.querySelector("[data-plugin-dialog-submit]");
  const controls = root.querySelector("[data-plugin-controls]");
  const state = { ready: false, busy: false, plugins: [], connections: [], admin: [],
    catalog: null, auth: null, view: root.dataset.view, search: "" };

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function localError(message) { return Object.assign(new Error(message), { safeMessage: message }); }
  function errorText(error) {
    if (error.safeMessage) return error.safeMessage;
    return ({
      401: "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
      403: "Tài khoản hiện không có quyền thực hiện thao tác này.",
      404: "Plugin, kết nối hoặc tính năng không còn khả dụng. Hãy tải lại.",
      409: "Dữ liệu hoặc quyền đã thay đổi. Đóng hộp thoại và tải lại trước khi thử lại.",
      422: "Thông tin chưa hợp lệ. Kiểm tra manifest, tài khoản và các quyền phụ thuộc.",
      503: "Dịch vụ chưa sẵn sàng. Liên hệ quản trị viên để kiểm tra cấu hình.",
    })[error.status] || "Không thể hoàn tất yêu cầu. Kiểm tra kết nối và tải lại dữ liệu.";
  }
  function announce(message, error = false) {
    status.textContent = message;
    status.classList.toggle("is-error", error);
  }
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(45000), ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    if (response.status === 401) window.location.assign("/login");
    if (!response.ok) throw Object.assign(new Error("Plugin request failed"), { status: response.status });
    return response.json();
  }
  function busy(value) {
    state.busy = value;
    if (controls) controls.disabled = value;
    dialog.querySelector("[data-plugin-dialog-content]").inert = value;
    submit.disabled = value || !state.ready;
    root.setAttribute("aria-busy", String(value));
  }
  function action(label, handler, { primary = false, danger = false, disabled = false } = {}) {
    const button = node("button", primary ? "plugin-primary" : "ghost-button", label);
    button.type = "button";
    button.disabled = disabled || !state.ready;
    if (danger) button.classList.add("plugin-danger");
    button.addEventListener("click", handler);
    return button;
  }
  function empty(title, description) {
    const box = node("div", "plugins-empty");
    box.append(node("span", "plugins-empty-mark", "⊞"), node("h3", "", title), node("p", "", description));
    return box;
  }
  function closeDialog() {
    dialog.close();
    content.replaceChildren();
    form.onsubmit = null;
  }
  dialog.querySelector("[data-plugin-dialog-close]").addEventListener("click", closeDialog);
  dialog.querySelector("[data-plugin-dialog-cancel]").addEventListener("click", closeDialog);
  dialog.addEventListener("close", () => { if (!dialog.open) content.replaceChildren(); });
  function openDialog(title, label, onSubmit) {
    content.replaceChildren();
    dialog.querySelector("h2").textContent = title;
    dialogStatus.textContent = "";
    dialogStatus.classList.remove("is-error");
    submit.textContent = label || "Lưu";
    submit.hidden = !onSubmit;
    submit.disabled = state.busy || !state.ready;
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (state.busy || !state.ready || !onSubmit) return;
      try { await onSubmit(); }
      catch (error) {
        dialogStatus.textContent = errorText(error);
        dialogStatus.classList.add("is-error");
      }
    };
    dialog.showModal();
  }
  function confirmAction(title, description, operation) {
    openDialog(title, "Xác nhận", operation);
    content.append(node("p", "plugins-note", description));
  }
  function field(labelText, type = "text") {
    const label = node("label", "", labelText);
    const input = node(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type;
    input.required = true;
    label.append(input);
    return { label, input };
  }
  async function collection(url, key, snapshot) {
    const rows = [];
    // Bounded pagination; an incomplete or mixed-version snapshot cannot enable mutations.
    for (let offset = 0; offset < 5000; offset += 100) {
      const page = await request(`${url}?limit=100&offset=${offset}`);
      if (snapshot.auth === null) { snapshot.auth = page.auth_version; snapshot.catalog = page.catalog_version; }
      if (snapshot.auth !== page.auth_version || snapshot.catalog !== page.catalog_version) {
        throw Object.assign(new Error("Snapshot changed"), { status: 409 });
      }
      rows.push(...page[key]);
      if (page[key].length < 100) return rows;
    }
    throw localError("Danh sách quá lớn. Liên hệ quản trị viên để kiểm tra catalog.");
  }
  async function load(message = "Đã cập nhật danh sách plugins.") {
    if (state.busy) return;
    busy(true); state.ready = false;
    retry.hidden = true;
    announce("Đang tải plugins và connections…");
    try {
      const snapshot = { auth: null, catalog: null };
      const plugins = await collection("/api/plugins", "plugins", snapshot);
      const connections = await collection("/api/plugin-connections", "connections", snapshot);
      const admin = root.dataset.admin === "true"
        ? await collection("/api/admin/plugins", "plugins", snapshot) : [];
      Object.assign(state, { plugins, connections, admin, auth: snapshot.auth,
        catalog: snapshot.catalog, ready: true });
      announce(message);
    } catch (error) {
      announce(errorText(error), true);
      retry.hidden = false;
    } finally { busy(false); render(); }
  }
  async function mutate(url, method, payload, message) {
    if (state.busy || !state.ready) return;
    busy(true);
    try {
      await request(url, { method, body: JSON.stringify(payload) });
      closeDialog();
    } catch (error) {
      // Never repeat a mutation, even after a network failure: its outcome may be unknown.
      state.ready = false;
      announce(errorText(error), true);
      retry.hidden = false;
      throw error;
    } finally { busy(false); render(); }
    await load(message);
  }
  const connectionStatus = {
    configured: ["Đã cấu hình · Đang bật", "Agent có thể dùng trong run mới khi dịch vụ sẵn sàng."],
    connection_disabled: ["Kết nối đang tắt", "Bật khi bạn muốn Agent sử dụng các quyền đã được cấp."],
    plugin_disabled: ["Plugin đang tắt", "Quản trị viên cần bật plugin trước khi bạn sử dụng."],
    grant_revoked: ["Chưa có quyền sử dụng", "Liên hệ quản trị viên để được cấp lại quyền."],
    version_mismatch: ["Cần cập nhật quyền / kết nối", "Release đã thay đổi. Quản trị viên cấp lại quyền, sau đó cấu hình và bật lại kết nối."],
    credentials_missing: ["Thiếu thông tin xác thực", "Thêm credential được yêu cầu trước khi bật kết nối."],
  };
  function baseCard(name, id, version, description) {
    const card = node("article", "plugin-card");
    const header = node("header");
    const heading = node("div"); heading.append(node("h3", "", name), node("p", "plugin-id", id));
    header.append(heading, node("span", "plugin-badge", `v${version}`));
    card.append(header, node("p", "plugin-description", description));
    return card;
  }
  function renderMine() {
    const list = root.querySelector("[data-plugin-list]");
    list.replaceChildren();
    const plugins = state.plugins.filter(p => `${p.name} ${p.id} ${p.description}`.toLocaleLowerCase("vi").includes(state.search));
    const configured = state.connections.filter(c => c.effective_enabled).length;
    root.querySelector("[data-plugin-count]").textContent = `${state.plugins.length} plugin được cấp · ${configured} kết nối đang bật`;
    if (!plugins.length) list.append(empty(state.search ? "Không tìm thấy plugin" : "Chưa có plugin được cấp quyền",
      state.search ? "Thử tên khác hoặc xóa bộ lọc tìm kiếm." : "Khi quản trị viên cấp quyền, plugin sẽ xuất hiện tại đây để bạn tạo kết nối."));
    for (const plugin of plugins) {
      const connection = state.connections.find(c => c.plugin_id === plugin.id);
      const currentGrant = plugin.grant_enabled && plugin.grant_manifest_version === plugin.version && plugin.capability_ids.length > 0;
      const card = baseCard(plugin.name, plugin.id, plugin.version, plugin.description);
      let code = connection?.status || (!plugin.plugin_enabled ? "plugin_disabled" : !currentGrant ? "grant_revoked" : "new");
      const [title, hint] = connectionStatus[code] || ["Chưa tạo kết nối", "Tạo kết nối riêng cho tài khoản của bạn; kết nối mới mặc định tắt."];
      const box = node("div", "plugin-state"); box.append(node("strong", "", title), node("p", "", hint));
      card.append(box);
      const details = node("details", "plugin-capabilities");
      details.append(node("summary", "", `${plugin.capability_ids.length} quyền được cấp`));
      const items = node("ul");
      for (const id of plugin.capability_ids) items.append(node("li", "", id));
      details.append(items); card.append(details);
      const buttons = node("div", "plugin-actions");
      if (!connection) {
        buttons.append(action("Tạo kết nối", () => confirmAction(`Kết nối ${plugin.name}`,
          "Tạo kết nối cho tài khoản này. Bạn sẽ chủ động bật sau khi cấu hình xong.",
          () => mutate("/api/plugin-connections", "POST", { plugin_id: plugin.id, expected_auth_version: state.auth }, "Đã tạo kết nối; hiện đang tắt.")),
        { primary: true, disabled: !currentGrant || !plugin.plugin_enabled }));
      } else {
        const enable = !connection.enabled;
        const canEnable = currentGrant && plugin.plugin_enabled && !["credentials_missing", "grant_revoked", "plugin_disabled"].includes(code);
        buttons.append(action(enable ? "Bật kết nối" : "Tắt kết nối", () => confirmAction(
          `${enable ? "Bật" : "Tắt"} ${plugin.name}`, enable
            ? "Cho phép Agent dùng các quyền đã cấp trong cuộc trò chuyện mới."
            : "Chặn lời gọi tiếp theo của kết nối. Tác vụ đã gửi tới dịch vụ có thể vẫn đang xử lý.",
          () => mutate(`/api/plugin-connections/${encodeURIComponent(connection.id)}/enabled`, "PATCH",
            { enabled: enable, expected_auth_version: state.auth }, enable ? "Đã bật kết nối." : "Đã tắt kết nối.")),
        { primary: enable, disabled: enable && !canEnable }));
        if (plugin.credential_requirements?.length || connection.credential_references.length) {
          buttons.append(action("Thông tin xác thực", () => credentialsDialog(plugin, connection)));
        }
      }
      card.append(buttons); list.append(card);
    }
  }
  function renderAdmin() {
    const list = root.querySelector("[data-plugin-admin-list]");
    if (!list) return;
    list.replaceChildren();
    if (!state.admin.length) list.append(empty("Catalog chưa có plugin", "Đăng ký manifest đã được review để bắt đầu. Plugin mới mặc định tắt và chưa cấp quyền cho ai."));
    for (const item of state.admin) {
      const plugin = item.manifest;
      const card = baseCard(plugin.name, plugin.id, plugin.version, plugin.description);
      card.append(node("span", `plugin-badge ${item.enabled ? "is-ready" : "is-warning"}`, item.enabled ? "Đang bật toàn hệ thống" : "Đang tắt toàn hệ thống"));
      const buttons = node("div", "plugin-actions");
      buttons.append(action(item.enabled ? "Tắt plugin" : "Bật plugin", () => confirmAction(
        `${item.enabled ? "Tắt" : "Bật"} ${plugin.name}`, item.enabled
          ? "Tắt plugin cho mọi tài khoản và chặn lời gọi tiếp theo. Tác vụ đã gửi có thể vẫn đang xử lý."
          : "Cho phép các tài khoản đã được cấp quyền và đã bật connection sử dụng plugin.",
        () => mutate(`/api/admin/plugins/${encodeURIComponent(plugin.id)}/enabled`, "PATCH",
          { enabled: !item.enabled, expected_catalog_version: state.catalog }, "Đã cập nhật trạng thái plugin.")), { danger: item.enabled }),
      action("Cấp quyền", () => grantsDialog(plugin)),
      action("Cập nhật manifest", () => manifestDialog(plugin)));
      card.append(buttons); list.append(card);
    }
  }
  function render() {
    renderMine(); renderAdmin();
    for (const panel of root.querySelectorAll("[data-plugin-panel]")) panel.hidden = panel.dataset.pluginPanel !== state.view;
    for (const button of root.querySelectorAll("[data-plugin-view]")) button.setAttribute("aria-pressed", String(button.dataset.pluginView === state.view));
    const register = root.querySelector("[data-plugin-register]");
    if (register) register.disabled = !state.ready;
  }
  function manifestDialog(manifest = null) {
    const { label, input } = field("Manifest JSON đã review", "textarea");
    input.maxLength = 262144;
    input.spellcheck = false;
    input.value = manifest ? JSON.stringify(manifest, null, 2) : "";
    input.placeholder = '{ "id": "example.plugin", … }';
    openDialog(manifest ? "Cập nhật release plugin" : "Đăng ký plugin", "Lưu manifest", async () => {
      let value;
      try { value = JSON.parse(input.value); } catch { throw localError("Manifest phải là JSON hợp lệ."); }
      if (!value || typeof value.id !== "string" || !/^[a-zA-Z0-9_.-]+$/.test(value.id)) throw localError("Manifest cần có id hợp lệ.");
      if (manifest && value.id !== manifest.id) throw localError("Không đổi id khi cập nhật plugin này.");
      await mutate(`/api/admin/plugins/${encodeURIComponent(value.id)}`, "PUT",
        { manifest: value, expected_catalog_version: state.catalog }, "Đã lưu manifest. Kiểm tra trạng thái và cấp lại quyền nếu đổi release.");
    });
    content.append(node("p", "plugins-note", "Release mới sẽ tắt plugin, connections và thu hồi grant/credential cũ. Cần review lại và cấp quyền trước khi dùng. Manifest không cài đặt server hay package."), label);
  }
  function credentialsDialog(plugin, connection) {
    openDialog(`Xác thực · ${plugin.name}`, null, null);
    content.append(node("p", "plugins-note", "Credential chỉ được gửi để lưu, không được hiển thị lại. Sau khi lưu hoặc thu hồi, kiểm tra và chủ động bật lại kết nối."));
    for (const requirement of plugin.credential_requirements || []) {
      const section = node("section", "plugin-reference");
      section.append(node("strong", "", `${requirement.server_id} / ${requirement.slot}`));
      const active = connection.credential_references.some(ref => ref.server_id === requirement.server_id && ref.slot === requirement.slot && ref.manifest_version === plugin.version);
      section.append(node("p", "", active ? "Đã có credential. Bạn có thể thay thế hoặc thu hồi bên dưới." : "Chưa có credential cho release hiện tại."));
      if (requirement.transport !== "streamable_http") {
        section.append(node("p", "", "Máy chủ này cần được quản trị viên cấu hình tại host."));
      } else if (requirement.kind === "oauth2") {
        section.append(action("Ủy quyền OAuth", async () => {
          if (state.busy || !state.ready) return;
          busy(true);
          try {
            const result = await request(`/api/plugin-connections/${encodeURIComponent(connection.id)}/oauth/begin`,
              { method: "POST", body: JSON.stringify({ server_id: requirement.server_id, slot: requirement.slot, expected_auth_version: state.auth }) });
            const url = new URL(result.authorization_url);
            if (url.protocol !== "https:" || url.username || url.password) throw localError("Địa chỉ ủy quyền chưa hợp lệ. Liên hệ quản trị viên.");
            window.location.assign(url.href);
          } catch (error) { state.ready = false; submit.disabled = true; dialogStatus.textContent = errorText(error); dialogStatus.classList.add("is-error"); retry.hidden = false; }
          finally { busy(false); }
        }));
      } else {
        const secret = field(requirement.kind === "api_key" ? "API key mới" : "Token mới", "password");
        secret.input.required = false;
        secret.input.maxLength = 16384;
        secret.input.autocomplete = "off";
        secret.input.spellcheck = false;
        const save = action("Lưu credential", async () => {
          const value = secret.input.value;
          secret.input.value = "";
          if (!value || !/^[\x21-\x7e]+$/.test(value)) { dialogStatus.textContent = "Credential không được trống hoặc chứa khoảng trắng."; return; }
          try {
            await mutate(`/api/plugin-connections/${encodeURIComponent(connection.id)}/credentials`, "POST",
              { server_id: requirement.server_id, slot: requirement.slot, value, expected_auth_version: state.auth }, "Đã lưu credential. Kết nối đang tắt; hãy bật khi sẵn sàng.");
          } catch (error) { dialogStatus.textContent = errorText(error); dialogStatus.classList.add("is-error"); }
        });
        section.append(secret.label, node("p", "plugins-note", "Không lưu credential trong trình duyệt. Chỉ nhập credential dành cho dịch vụ này."), save);
      }
      content.append(section);
    }
    for (const reference of connection.credential_references) {
      const section = node("section", "plugin-reference");
      section.append(node("p", "", `Credential đang lưu: ${reference.server_id} / ${reference.slot} · release ${reference.manifest_version}`),
        action("Thu hồi credential", () => {
          closeDialog();
          confirmAction("Thu hồi credential", "Vô hiệu hóa credential và tắt connection này. Cần xác thực lại trước khi bật.",
            () => mutate(`/api/plugin-connections/${encodeURIComponent(connection.id)}/credentials/${encodeURIComponent(reference.id)}/revoke`,
              "POST", { expected_auth_version: state.auth }, "Đã thu hồi credential và tắt kết nối."));
        }, { danger: true }));
      content.append(section);
    }
  }
  function capabilityOptions(manifest) {
    const result = [];
    for (const server of manifest.servers) for (const tool of server.tools) {
      result.push({ id: `${manifest.namespace}.tool.${server.server_id}.${tool.name}`, name: tool.name,
        hint: `${server.server_id} · ${tool.policy.side_effect}${tool.policy.requires_approval ? " · cần phê duyệt" : ""}`, dependencies: [] });
    }
    for (const skill of manifest.skills || []) result.push({ id: `${manifest.namespace}.skill.${skill.name}`,
      name: skill.name, hint: "Skill", dependencies: (skill.required_tools || []).map(t => `${manifest.namespace}.tool.${t.server_id}.${t.tool_name}`) });
    return result;
  }
  async function grantsDialog(plugin) {
    if (state.busy || !state.ready) return;
    busy(true);
    let grants;
    try {
      grants = await collection(`/api/admin/plugins/${encodeURIComponent(plugin.id)}/grants`, "grants", { auth: state.auth, catalog: state.catalog });
    } catch (error) { state.ready = false; announce(errorText(error), true); retry.hidden = false; return; }
    finally { busy(false); render(); }
    const user = field("User ID (UUID)");
    user.input.pattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
    user.input.placeholder = "UUID tài khoản cần cấp quyền";
    const enabled = field("Cho phép sử dụng plugin", "checkbox"); enabled.input.checked = true; enabled.input.required = false; enabled.label.className = "plugin-check";
    const scope = node("fieldset"); scope.append(node("legend", "", "Quyền được cấp"));
    const options = capabilityOptions(plugin), inputs = new Map();
    for (const item of options) {
      const entry = field(`${item.name} · ${item.hint}`, "checkbox");
      entry.label.className = "plugin-check"; entry.input.required = false; entry.input.value = item.id;
      inputs.set(item.id, entry.input); scope.append(entry.label);
      entry.input.addEventListener("change", () => {
        if (entry.input.checked) for (const dep of item.dependencies) if (inputs.has(dep)) inputs.get(dep).checked = true;
        if (!entry.input.checked) for (const other of options) if (other.dependencies.includes(item.id)) inputs.get(other.id).checked = false;
      });
    }
    function choose(id) {
      user.input.value = id;
      const grant = grants.find(g => g.user_id === id);
      enabled.input.checked = grant ? grant.enabled : true;
      for (const [key, input] of inputs) input.checked = !!grant?.capability_ids.includes(key);
    }
    user.input.addEventListener("change", () => choose(user.input.value.trim()));
    openDialog(`Cấp quyền · ${plugin.name}`, "Lưu quyền", async () => {
      const ids = [...inputs].filter(([, input]) => input.checked).map(([id]) => id);
      if (enabled.input.checked && !ids.length) throw localError("Chọn ít nhất một quyền trước khi cho phép sử dụng.");
      await mutate(`/api/admin/plugins/${encodeURIComponent(plugin.id)}/grants/${encodeURIComponent(user.input.value.trim())}`, "PUT",
        { enabled: enabled.input.checked, capability_ids: ids, expected_auth_version: state.auth }, "Đã cập nhật quyền. Người dùng cần kiểm tra và bật lại connection.");
    });
    content.append(node("p", "plugins-note", "Chọn Skill sẽ chọn cả tool bắt buộc. Role vẫn do API kiểm tra. Thay đổi quyền sẽ tắt connection của tài khoản được chọn."));
    if (grants.length) {
      content.append(node("h3", "", "Tài khoản đã có grant"));
      for (const grant of grants) {
        const row = node("div", "plugin-grant-row");
        row.append(node("p", "", `${grant.user_id} · ${grant.enabled ? "Đang cấp" : "Đã thu hồi"} · ${grant.capability_ids.length} quyền`),
          action("Chọn", () => choose(grant.user_id)));
        content.append(row);
      }
    }
    content.append(user.label, enabled.label, scope);
  }
  async function callback() {
    if (window.location.pathname !== "/plugins/oauth/callback") return "Đã cập nhật danh sách plugins.";
    const params = new URLSearchParams(window.location.search);
    // Remove authorization material before any subsequent UI request/navigation.
    window.history.replaceState(null, "", "/plugins");
    if (params.has("error") || !params.get("state") || !params.get("code")) throw localError("Ủy quyền chưa hoàn tất. Bạn có thể bắt đầu lại từ kết nối.");
    await request("/api/plugins/oauth/complete", { method: "POST", body: JSON.stringify({
      state: params.get("state"), code: params.get("code"), issuer: params.get("iss") }) });
    return "Đã hoàn tất OAuth. Kiểm tra và bật lại connection khi sẵn sàng.";
  }
  if (root.dataset.enabled !== "true") {
    if (window.location.pathname === "/plugins/oauth/callback") window.history.replaceState(null, "", "/plugins");
    return;
  }
  retry.addEventListener("click", () => load());
  root.querySelector("[data-plugin-refresh]").addEventListener("click", () => load());
  root.querySelector("[data-plugin-search]").addEventListener("input", event => {
    state.search = event.target.value.trim().toLocaleLowerCase("vi"); renderMine();
  });
  for (const button of root.querySelectorAll("[data-plugin-view]")) button.addEventListener("click", () => {
    state.view = button.dataset.pluginView; render();
  });
  root.querySelector("[data-plugin-register]")?.addEventListener("click", () => manifestDialog());
  (async () => {
    let message;
    try { message = await callback(); }
    catch (error) { message = errorText(error); await load(); announce(message, true); return; }
    await load(message);
  })();
})();
