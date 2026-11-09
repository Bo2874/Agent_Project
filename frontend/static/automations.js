(() => {
  const statuses = { queued: "Đang xếp hàng", running: "Đang chạy", waiting_approval: "Cần phê duyệt", cancelling: "Đang yêu cầu dừng", needs_reconciliation: "Cần kiểm tra kết quả", succeeded: "Hoàn tất", failed: "Chưa hoàn tất", cancelled: "Đã dừng", skipped_overlap: "Bỏ qua vì lượt trước còn hoạt động", missed: "Đã lỡ lịch" };
  const active = value => ["queued", "running", "waiting_approval", "cancelling", "needs_reconciliation"].includes(value);
  const el = (tag, text, cls) => { const item = document.createElement(tag); if (text != null) item.textContent = text; if (cls) item.className = cls; return item; };
  const button = (text, handler, attrs = {}) => { const item = el("button", text, "ghost-button"); item.type = "button"; item.onclick = handler; Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, value)); return item; };
  async function requestJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    if (response.status === 401) { location.assign("/login"); throw new Error("Phiên đăng nhập đã hết hạn."); }
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Vui lòng kiểm tra lại cấu hình.");
    return data;
  }
  const json = value => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  const requestId = () => crypto.randomUUID ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, "0")).join("");
  function safeGoogleLink(url) { try { const u = new URL(url); return u.protocol === "https:" && ["docs.google.com", "drive.google.com"].includes(u.hostname) && !u.username && !u.password ? u.href : null; } catch { return null; } }
  function mount(root, options = {}) {
    const request = options.requestJson || requestJson;
    root.innerHTML = `<section class="artifact-primary shell automation-primary">
      <header class="automation-hero"><div><p class="eyebrow">KHÔNG GIAN LÀM VIỆC</p><h1>Automations</h1><p class="lead">Giao việc một lần. Xem kết quả khi bạn quay lại.</p></div><button type="button" class="primary-button" data-create-automation>＋ Tạo Automation</button></header>
      <p role="status" aria-live="polite" data-auto-status></p>
      <div class="automation-grid"><aside class="automation-list" aria-label="Danh sách nhiệm vụ" data-automation-list></aside><section class="automation-detail" aria-label="Chi tiết nhiệm vụ" data-automation-detail></section></div>
      <dialog class="automation-dialog" data-edit-dialog><form data-auto-form>
        <div class="automation-heading"><h2 data-form-title>Tạo Automation</h2><button type="button" class="workspace-icon" data-dismiss-edit aria-label="Đóng">×</button></div>
        <label>Tên nhiệm vụ<input name="name" required maxlength="160" placeholder="Bản tin buổi sáng"></label>
        <label>Yêu cầu Agent thực hiện<textarea name="instruction" required maxlength="20000" rows="4" placeholder="Tìm tin tức mới, tổng hợp và lưu kết quả…"></textarea></label>
        <div class="automation-fields"><label>Kiểu lịch<select name="kind"><option value="daily">Hằng ngày</option><option value="weekly">Hằng tuần</option><option value="once">Một lần</option></select></label><label>Múi giờ<input name="timezone" required value="Asia/Ho_Chi_Minh" list="automation-zones"></label></div>
        <datalist id="automation-zones"><option value="Asia/Ho_Chi_Minh"><option value="UTC"><option value="America/New_York"><option value="Europe/London"></datalist>
        <div class="automation-fields"><label data-time-field>Giờ chạy<input name="local_time" type="time" value="08:00"></label><label data-week-field hidden>Thứ<select name="weekday"><option value="0">Thứ Hai</option><option value="1">Thứ Ba</option><option value="2">Thứ Tư</option><option value="3">Thứ Năm</option><option value="4">Thứ Sáu</option><option value="5">Thứ Bảy</option><option value="6">Chủ Nhật</option></select></label><label data-once-field hidden>Ngày và giờ trong múi giờ đã chọn<input name="at_local" type="datetime-local"></label></div>
        <fieldset><legend>File làm việc trong ứng dụng</legend><div data-artifact-inputs></div><div class="automation-checks"><label><input type="checkbox" name="artifact_action" value="inspect">Đọc file đã chọn</label><label><input type="checkbox" name="artifact_action" value="create">Tạo file mới</label><label><input type="checkbox" name="artifact_action" value="edit">Sửa file đã chọn và file của lượt này</label></div></fieldset>
        <fieldset><legend>Google Workspace</legend><p class="document-hint">Chỉ dùng những file bạn đã chọn trong Connectors. Cấp từng thao tác cho nhiệm vụ này.</p><div data-google-grants></div><button type="button" class="document-text-button" data-add-google>＋ Thêm file Google</button><div data-new-google></div></fieldset>
        <p class="document-hint">Tắt tab không dừng nhiệm vụ. Tắt lịch không chạy bù khi bật lại. Python tùy ý vẫn cần phê duyệt từng lần.</p>
        <p role="status" aria-live="polite" data-form-status></p><section class="automation-review" data-review hidden></section>
        <div class="automation-actions"><button type="button" class="ghost-button" data-review-automation>Xem lại nhiệm vụ và quyền</button><button type="submit" class="primary-button" data-save-automation hidden>Lưu bản nháp</button></div>
      </form></dialog>
      <dialog class="automation-dialog" data-pause-dialog><h2>Tắt lịch này?</h2><p>Lượt hiện tại vẫn đang hoạt động. Chọn cách xử lý:</p><div class="automation-actions"><button type="button" class="ghost-button" data-pause-keep>Tắt lịch, giữ lượt hiện tại</button><button type="button" class="primary-button" data-pause-stop>Tắt lịch và dừng lượt</button><button type="button" class="document-text-button" data-pause-cancel>Quay lại</button></div></dialog>
    </section>`;
    const $ = selector => root.querySelector(selector);
    const form = $("[data-auto-form]"), editor = $("[data-edit-dialog]"), pauseDialog = $("[data-pause-dialog]");
    const state = { autos: [], runs: [], auto: null, run: null, autoId: null, runId: null, inputs: { connections: [], google_files: [], artifacts: [] }, draft: null, editing: null, source: null, sequence: 0, formSequence: 0, destroyed: false, previewText: "" };
    const manualKeys = new Map(), manualPending = new Set();
    const panel = window.createArtifactPanel && window.ArtifactWorkspace ? window.createArtifactPanel(root, { requestJson: request }) : null;
    if (panel) window.ArtifactWorkspace.onOpen = (id, context) => panel.open(id, context);
    const format = (value, zone = state.auto?.schedule?.timezone || "Asia/Ho_Chi_Minh") => value ? new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short", timeZone: zone }).format(new Date(value)) : "Chưa có mốc tiếp theo";
    function fail(error) { $("[data-auto-status]").textContent = error.message || "Không thể tải dữ liệu."; }
    async function action(work) { try { await work(); window.dispatchEvent(new Event("automation-updated")); } catch (error) { fail(error); } }
    async function runManual(auto, control) {
      if (manualPending.has(auto.id)) return;
      manualPending.add(auto.id); control.disabled = true;
      const key = manualKeys.get(auto.id) || requestId(); manualKeys.set(auto.id, key);
      try {
        const value = await request(`/api/automations/${auto.id}/run-now`, json({ idempotency_key: key }));
        manualKeys.delete(auto.id);
        if (state.autoId === auto.id) { state.runId = value.id; await refresh(); }
      } finally {
        manualPending.delete(auto.id);
        if (!state.destroyed && state.auto?.id === state.autoId) render();
      }
    }
    function changedSelection() { state.sequence += 1; panel?.close(); state.source?.close(); state.source = null; }
    function locationState() { if (location.protocol.startsWith("http")) { const url = new URL(location.href); if (state.autoId) url.searchParams.set("automation", state.autoId); if (state.runId) url.searchParams.set("run", state.runId); else url.searchParams.delete("run"); history.replaceState(null, "", url); } }
    function outputLink(parent, url, label) { const target = safeGoogleLink(url); if (!target) return; const link = el("a", label, "text-link"); link.href = target; link.target = "_blank"; link.rel = "noopener noreferrer"; parent.append(link); }
    function connectEvents(run) {
      const key = run?.agent_run_id;
      if (!options.events && options.events !== undefined || !key || !["running", "queued", "waiting_approval", "cancelling"].includes(run.status)) { state.source?.close(); state.source = null; return; }
      if (state.source?.runKey === key) return;
      state.source?.close();
      const source = new EventSource(`/api/conversations/${run.conversation_id}/runs/${key}/events`);
      source.runKey = key; state.source = source;
      source.addEventListener("agent_event", event => { if (state.run?.agent_run_id !== key) return; try { const data = JSON.parse(event.data); const target = $("[data-run-progress]"); if (target) target.textContent = data.message || data.tool_name || "Agent đang thực hiện nhiệm vụ…"; if (data.approval) void refresh(); } catch { /* Durable poll restores state. */ } });
      ["done", "error", "cancelled", "snapshot"].forEach(kind => source.addEventListener(kind, () => { source.close(); if (state.source === source) state.source = null; void refresh(); window.dispatchEvent(new Event("automation-updated")); }));
    }
    function render() {
      const list = $("[data-automation-list]"), detail = $("[data-automation-detail]"); list.replaceChildren(); detail.replaceChildren();
      if (!state.autos.length) list.append(el("p", "Chưa có nhiệm vụ theo lịch.", "document-hint"));
      for (const auto of state.autos) {
        const item = button("", () => action(async () => { changedSelection(); state.autoId = auto.id; state.runId = null; await refresh(); }), { "data-automation-id": auto.id });
        item.className = "automation-list-item"; if (auto.id === state.autoId) item.setAttribute("aria-current", "true");
        item.append(el("strong", auto.name), el("span", auto.enabled ? "Lịch đang bật" : "Lịch đang tắt")); list.append(item);
      }
      const auto = state.auto;
      if (!auto) { detail.append(el("h2", "Bạn muốn giao việc gì?"), el("p", "Tạo nhiệm vụ, chạy thử rồi bật lịch khi đã sẵn sàng.", "lead")); return; }
      const top = el("div", null, "automation-heading"); top.append(el("h2", auto.name), button("Chỉnh sửa", () => { void openEditor(auto); })); detail.append(top);
      detail.append(el("p", auto.instruction, "automation-instruction"));
      const schedule = el("p", `${auto.enabled ? "Lịch đang bật" : "Lịch đang tắt"} · ${auto.schedule.timezone} · ${auto.enabled ? format(auto.next_run_at) : "Chỉ chạy khi bạn bấm Chạy ngay"}`, "document-hint"); detail.append(schedule);
      const controls = el("div", null, "automation-actions");
      const manual = button("Chạy ngay", () => action(() => runManual(auto, manual)), { "data-run-now": "" });
      manual.disabled = manualPending.has(auto.id) || state.runs.some(run => active(run.status));
      controls.append(manual, button(auto.enabled ? "Tắt lịch" : "Bật lịch", () => action(async () => {
        if (auto.enabled && state.runs.some(run => active(run.status) && run.status !== "queued")) { pauseDialog.showModal(); return; }
        await request(`/api/automations/${auto.id}/enabled`, json({ enabled: !auto.enabled, stop_active: false })); await refresh();
      }), { "data-toggle-schedule": "" })); detail.append(controls);
      detail.append(el("h3", "Lịch sử thực hiện"));
      const historyList = el("div", null, "automation-runs");
      if (!state.runs.length) historyList.append(el("p", "Chưa có lượt chạy. Hãy chạy thử trước khi bật lịch.", "document-hint"));
      for (const run of state.runs) { const item = button(`${statuses[run.status] || run.status} · ${format(run.scheduled_for || run.created_at)}`, () => action(async () => { changedSelection(); state.runId = run.id; await refresh(); }), { "data-run-id": run.id }); if (run.id === state.runId) item.setAttribute("aria-current", "true"); historyList.append(item); }
      detail.append(historyList);
      const run = state.run; if (!run) return;
      const box = el("section", null, "automation-run-detail"); box.append(el("h3", statuses[run.status] || run.status));
      box.append(el("p", `Phiên bản ${run.revision_number || "—"} · ${run.trigger === "manual" ? "Chạy thử / thủ công" : "Theo lịch"}`, "document-hint"));
      if (run.instruction) { const source = el("details"); source.append(el("summary", "Yêu cầu của lượt này"), el("p", run.instruction)); box.append(source); }
      const result = run.result || {};
      if (result.missed_count) box.append(el("p", `Đã bỏ lỡ ${result.missed_count} mốc lịch. Không chạy bù.`));
      if (result.total_tokens != null) box.append(el("p", `${result.total_tokens.toLocaleString("vi-VN")} token đã ghi nhận${result.usage_unknown ? " · còn phần sử dụng chưa xác định" : ""}`, "document-hint"));
      if (active(run.status) && run.status !== "needs_reconciliation") { const stop = button("Dừng lượt chạy", () => action(async () => { await request(`/api/automations/runs/${run.id}/stop`, json({})); await refresh(); }), { "data-stop-run": "" }); stop.disabled = run.status === "cancelling"; box.append(stop); }
      const progress = el("p", "", "document-hint"); progress.dataset.runProgress = ""; progress.setAttribute("aria-live", "polite"); box.append(progress);
      for (const approval of run.pending_approvals || []) { const card = el("section", null, "automation-approval"); card.append(el("h4", `Phê duyệt ${approval.tool_name}`), el("pre", JSON.stringify(approval.arguments, null, 2))); const actions = el("div", null, "automation-actions"); for (const [label, approved] of [["Cho phép lần này", true], ["Từ chối", false]]) actions.append(button(label, () => action(async () => { await request(`/api/conversations/${run.conversation_id}/runs/${run.agent_run_id}/approvals/${approval.id}`, json({ approved })); await refresh(); }))); card.append(actions); box.append(card); }
      if (run.status === "needs_reconciliation") { box.append(el("p", "Một hành động đã gửi nhưng chưa rõ kết quả. Hãy kiểm tra file đích. Kết thúc lượt không hoàn tác và không phát lại hành động.")); box.append(button("Tôi đã kiểm tra, kết thúc lượt", () => action(async () => { if (!confirm("Bạn đã kiểm tra kết quả ở file đích? Kết thúc lượt sẽ giữ nguyên phần kết quả chưa xác định.")) return; await request(`/api/automations/runs/${run.id}/close-unknown`, json({ confirmed: true })); await refresh(); }), { "data-close-unknown": "" })); }
      for (const item of result.google_results || []) outputLink(box, item.url, `Mở kết quả Google${item.updated_rows ? ` · ${item.updated_rows} hàng` : ""}`);
      for (const item of result.uncertain_actions || []) if (item.url) outputLink(box, item.url, "Kiểm tra file đích");
      if (result.artifact_results?.length) { const files = el("div"); if (window.renderArtifactCards) window.renderArtifactCards(files, result.artifact_results, run.conversation_id); else for (const ref of result.artifact_results) files.append(el("p", ref.name)); box.append(files); }
      if (state.previewText) box.append(el("pre", state.previewText, "automation-output"));
      if (result.message) box.append(el("p", result.message)); detail.append(box); connectEvents(run);
    }
    async function refresh() {
      const sequence = ++state.sequence;
      try {
        const list = await request("/api/automations"); if (sequence !== state.sequence || state.destroyed) return;
        state.autos = list.items; if (!state.autoId) state.autoId = list.items[0]?.id || null;
        if (!state.autoId) { state.auto = state.run = null; render(); return; }
        const id = state.autoId;
        const [auto, runs] = await Promise.all([request(`/api/automations/${id}`), request(`/api/automations/${id}/runs`)]);
        if (sequence !== state.sequence || state.destroyed || id !== state.autoId) return;
        state.auto = auto; state.runs = runs.items;
        if (!state.runId) state.runId = runs.items[0]?.id || null;
        const run = state.runId ? await request(`/api/automations/runs/${state.runId}`) : null;
        if (sequence !== state.sequence || state.destroyed) return;
        state.run = run; state.previewText = "";
        if (run?.conversation_id) { const messages = await request(`/api/conversations/${run.conversation_id}/messages`); if (sequence !== state.sequence || state.destroyed) return; state.previewText = messages.filter(item => item.role === "assistant").map(item => item.content).join("\n\n"); }
        render(); locationState();
      } catch (error) { if (sequence === state.sequence) fail(error); }
    }
    function updateScheduleFields() { const kind = form.elements.kind.value; $("[data-time-field]").hidden = kind === "once"; $("[data-week-field]").hidden = kind !== "weekly"; $("[data-once-field]").hidden = kind !== "once"; }
    function invalidateReview() { state.formSequence += 1; state.draft = null; $("[data-save-automation]").hidden = true; $("[data-review]").hidden = true; }
    function googleRow(preset = null) {
      const row = el("fieldset", null, "automation-google-row"), choose = el("select"); choose.setAttribute("aria-label", "File Google");
      const key = file => `${file.connection_id}/${file.file_id}`;
      let files = [...state.inputs.google_files];
      if (preset && !files.some(file => key(file) === key(preset))) files.push({ ...preset, name: "File đã chọn (cần kiểm tra kết nối)", mime_type: preset.action?.includes("doc") ? "application/vnd.google-apps.document" : "application/vnd.google-apps.spreadsheet" });
      for (const file of files) { const item = el("option", file.name); item.value = key(file); choose.append(item); }
      if (preset) choose.value = key(preset);
      const tabs = el("select"), range = el("input"), actions = el("div", null, "automation-checks"); tabs.setAttribute("aria-label", "Tab Google Sheets"); range.setAttribute("aria-label", "Vùng ô hoặc vùng dò bảng để thêm hàng"); range.placeholder = "A1:D100"; range.value = preset?.range_a1?.split("!").at(-1) || "A1:D100";
      const hint = el("p", "Quyền đọc dùng tab/vùng đã chọn. Quyền đọc và sửa toàn file cho phép Agent tự xác định ô, công thức và bố cục cần sửa. Quyền vùng cũ không tự nâng thành quyền toàn file.", "document-hint");
      const rowState = { value: null, token: 0 };
      async function load() {
        const token = ++rowState.token; const file = files.find(item => key(item) === choose.value); rowState.value = file;
        actions.replaceChildren(); tabs.replaceChildren(); if (!file) return;
        const sheet = file.mime_type === "application/vnd.google-apps.spreadsheet";
        const doc = file.mime_type === "application/vnd.google-apps.document";
        const allowed = sheet ? [["read_sheet", "Chỉ đọc vùng"], ["edit_sheet", "Đọc và sửa nội dung/bố cục toàn file"]] : doc ? [["read_doc", "Chỉ đọc"], ["edit_doc", "Đọc và sửa nội dung/bố cục"]] : [["read_file", "Tải và đọc file"], ["edit_file", "Đọc và cập nhật file cùng định dạng"]];
        if (preset && ["append_rows", "update_range", "set_formulas", "format_range"].includes(preset.action)) allowed.push([preset.action, `Quyền vùng cũ: ${preset.action} (cần chọn quyền toàn file để dùng luồng Python mới)`]);
        for (const [value, title] of allowed) { const label = el("label", title); const input = el("input"); input.type = "checkbox"; input.value = value; input.checked = preset?.action === value; label.prepend(input); actions.append(label); }
        tabs.hidden = range.hidden = hint.hidden = !sheet;
        if (sheet) {
          try { const data = await request(`/api/automations/inputs/google/${file.connection_id}/${file.file_id}/tabs`); if (rowState.token !== token || !row.isConnected) return; for (const tab of data.items) { const item = el("option", tab.title); item.value = tab.sheet_id; tabs.append(item); } if (preset?.sheet_id != null) tabs.value = preset.sheet_id; }
          catch (error) { if (rowState.token !== token) return; const item = el("option", preset?.range_a1?.split("!")[0]?.replace(/^'|'$/g, "").replace(/''/g, "'") || "Chọn lại file sau khi kết nối"); item.value = preset?.sheet_id ?? ""; tabs.append(item); $("[data-form-status]").textContent = error.message; }
        }
      }
      choose.onchange = () => { preset = null; invalidateReview(); void load(); };
      row.append(choose, tabs, range, actions, hint, button("Bỏ file này", () => { rowState.token += 1; row.remove(); invalidateReview(); }));
      row.collect = () => { const file = rowState.value; if (!file) return []; const sheet = file.mime_type === "application/vnd.google-apps.spreadsheet"; return [...actions.querySelectorAll("input:checked")].map(input => { const grant = { connection_id: file.connection_id, file_id: file.file_id, action: input.value }; if (sheet && input.value !== "edit_sheet") { if (!tabs.value) throw new Error("Hãy chọn tab của Google Sheet."); grant.sheet_id = Number(tabs.value); grant.range_a1 = `'${tabs.selectedOptions[0].textContent.replace(/'/g, "''")}'!${range.value.trim()}`; } return grant; }); };
      $("[data-google-grants]").append(row); void load();
    }
    async function openEditor(auto = null) {
      try {
        state.inputs = await request("/api/automations/inputs"); state.editing = auto?.id || null; form.reset(); invalidateReview(); $("[data-form-status]").textContent = "";
        const versions = new Map(state.inputs.artifacts.map(ref => [ref.version_id, ref]));
        for (const ref of auto?.artifact_inputs || []) if (!versions.has(ref.version_id)) versions.set(ref.version_id, ref);
        for (const id of auto?.artifact_version_ids || []) if (!versions.has(id)) versions.set(id, { version_id: id, name: "Phiên bản đã chọn, cần kiểm tra lại file", version_number: "—" });
        state.inputs.artifacts = [...versions.values()];
        const connections = new Map(state.inputs.connections.map(connection => [connection.id, connection]));
        for (const grant of auto?.grants || []) if (!connections.has(grant.connection_id)) connections.set(grant.connection_id, { id: grant.connection_id, account_name: "Tài khoản đã cấu hình, cần kiểm tra lại kết nối" });
        state.inputs.connections = [...connections.values()];
        $("[data-form-title]").textContent = auto ? "Chỉnh sửa Automation" : "Tạo Automation"; $("[data-save-automation]").textContent = auto ? "Lưu phiên bản mới" : "Lưu bản nháp";
        form.elements.name.value = auto?.name || ""; form.elements.instruction.value = auto?.instruction || ""; form.elements.kind.value = auto?.schedule.kind || "daily"; form.elements.timezone.value = auto?.schedule.timezone || "Asia/Ho_Chi_Minh"; form.elements.local_time.value = (auto?.schedule.local_time || "08:00").slice(0, 5); form.elements.weekday.value = auto?.schedule.weekday ?? 0;
        if (auto?.schedule.at) { const parts = new Intl.DateTimeFormat("sv-SE", { timeZone: auto.schedule.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(auto.schedule.at)); const value = name => parts.find(part => part.type === name).value; form.elements.at_local.value = `${value("year")}-${value("month")}-${value("day")}T${value("hour")}:${value("minute")}`; }
        updateScheduleFields(); $("[data-artifact-inputs]").replaceChildren(); $("[data-google-grants]").replaceChildren(); $("[data-new-google]").replaceChildren();
        for (const ref of state.inputs.artifacts) { const label = el("label", `${ref.name} · v${ref.version_number}`); const input = el("input"); input.type = "checkbox"; input.name = "artifact_version"; input.value = ref.version_id; input.checked = auto?.artifact_version_ids?.includes(ref.version_id) || false; label.prepend(input); $("[data-artifact-inputs]").append(label); }
        for (const input of form.querySelectorAll('[name="artifact_action"]')) input.checked = auto?.artifact_actions?.includes(input.value) || false;
        for (const grant of auto?.grants || []) if (grant.file_id) googleRow(grant);
        for (const connection of state.inputs.connections) { const group = el("section"); group.append(el("p", connection.account_email || connection.account_name || "Google đã kết nối")); for (const [value, title] of [["create_doc", "Tạo và soạn Docs mới của lượt này"], ["upload_artifact", "Lưu bản sao file lên Drive; giữ định dạng hoặc chuyển đổi theo yêu cầu"]]) { const label = el("label", title); const input = el("input"); input.type = "checkbox"; input.dataset.connection = connection.id; input.name = "new_google"; input.value = value; input.checked = auto?.grants?.some(grant => grant.connection_id === connection.id && grant.action === value && !grant.file_id) || false; label.prepend(input); group.append(label); } $("[data-new-google]").append(group); }
        $("[data-add-google]").disabled = !state.inputs.google_files.length;
        if (!state.inputs.connections.length) $("[data-new-google]").append(el("p", "Chưa có tài khoản Google sẵn sàng. Bạn vẫn có thể chạy nhiệm vụ và lưu kết quả trong ứng dụng.", "document-hint"));
        editor.showModal(); form.elements.name.focus();
      } catch (error) { fail(error); }
    }
    async function review() {
      try {
        if (!form.reportValidity()) return;
        const sequence = state.formSequence;
        const kind = form.elements.kind.value;
        const schedule = { kind, timezone: form.elements.timezone.value };
        if (kind === "once") schedule.at_local = form.elements.at_local.value; else { schedule.local_time = form.elements.local_time.value; if (kind === "weekly") schedule.weekday = Number(form.elements.weekday.value); }
        const preview = await request("/api/automations/schedules/preview", json(schedule));
        if (sequence !== state.formSequence || !editor.open) return;
        const grants = [...$("[data-google-grants]").children].flatMap(row => row.collect());
        for (const input of form.querySelectorAll('[name="new_google"]:checked')) grants.push({ connection_id: input.dataset.connection, action: input.value });
        state.draft = { name: form.elements.name.value.trim(), instruction: form.elements.instruction.value.trim(), schedule: preview.schedule, grants, artifact_version_ids: [...form.querySelectorAll('[name="artifact_version"]:checked')].map(input => input.value), artifact_actions: [...form.querySelectorAll('[name="artifact_action"]:checked')].map(input => input.value) };
        const box = $("[data-review]"); box.replaceChildren(el("h3", "Xem lại trước khi lưu"), el("strong", state.draft.name), el("p", state.draft.instruction), el("p", `Mốc tiếp theo: ${format(preview.next_run_at, schedule.timezone)} · ${schedule.timezone}`));
        for (const grant of grants) { const file = state.inputs.google_files.find(file => file.connection_id === grant.connection_id && file.file_id === grant.file_id); box.append(el("p", `${file?.name || "File mới trong Google"} · ${grant.action}${grant.range_a1 ? ` · ${grant.range_a1}` : ""}`)); }
        box.append(el("p", `Quyền file trong ứng dụng: ${state.draft.artifact_actions.join(", ") || "chờ phê duyệt khi cần"}. ${state.draft.artifact_version_ids.length} phiên bản file được chọn.`));
        if (!state.editing) box.append(el("p", "Lưu bản nháp chưa bật lịch. Bạn có thể Chạy ngay để thử."));
        box.hidden = false; $("[data-save-automation]").hidden = false; $("[data-form-status]").textContent = "";
      } catch (error) { $("[data-form-status]").textContent = error.message; }
    }
    form.addEventListener("input", invalidateReview); form.elements.kind.onchange = () => { updateScheduleFields(); invalidateReview(); };
    $("[data-create-automation]").onclick = () => { void openEditor(); }; $("[data-dismiss-edit]").onclick = () => editor.close(); $("[data-add-google]").onclick = () => googleRow(); $("[data-review-automation]").onclick = () => { void review(); };
    form.onsubmit = event => { event.preventDefault(); if (!state.draft) return; void action(async () => { const value = await request(state.editing ? `/api/automations/${state.editing}` : "/api/automations", { ...json(state.draft), method: state.editing ? "PATCH" : "POST" }); changedSelection(); state.autoId = value.id; state.runId = null; editor.close(); await refresh(); }); };
    const pause = stop => action(async () => { pauseDialog.close(); await request(`/api/automations/${state.autoId}/enabled`, json({ enabled: false, stop_active: stop })); await refresh(); });
    $("[data-pause-keep]").onclick = () => { void pause(false); }; $("[data-pause-stop]").onclick = () => { void pause(true); }; $("[data-pause-cancel]").onclick = () => pauseDialog.close();
    if (location.protocol.startsWith("http")) { const params = new URL(location.href).searchParams; state.autoId = params.get("automation"); state.runId = params.get("run"); }
    const timer = options.poll === false ? null : setInterval(() => { if (!document.hidden && !editor.open && !pauseDialog.open) void refresh(); }, 3000);
    return { ready: refresh(), refresh, destroy() { state.destroyed = true; state.sequence += 1; clearInterval(timer); state.source?.close(); panel?.close(); editor.close(); pauseDialog.close(); } };
  }
  window.AutomationWorkspace = { mount, requestJson };
  const root = document.querySelector("[data-automations-app]"); if (root) window.automationPage = mount(root);
})();
