/* Immutable versions; opening is always an explicit user action. */
(() => {
  const element = (tag, cls = "", text = "") => {
    const value = document.createElement(tag); value.className = cls; value.textContent = text; return value;
  };
  const button = (text, click, cls = "ghost-button") => {
    const value = element("button", cls, text); value.type = "button"; value.addEventListener("click", click); return value;
  };
  const statusText = state => ({ fresh: "Đã tính lại công thức", unsupported: "Có công thức chưa hỗ trợ tính lại", failed: "Chưa tính thành công", not_calculated: "Chưa xác minh độ mới của kết quả công thức" })[state] || "Chưa tính lại";
  window.createArtifactPanel = (root, { requestJson }) => {
    const panel = element("aside", "artifact-preview"); panel.hidden = true; panel.tabIndex = -1;
    panel.setAttribute("aria-label", "Xem file làm việc");
    const header = element("header", "artifact-preview-header");
    const name = element("h2"), closeButton = button(root.classList.contains("chat-shell") ? "← Quay lại chat" : "← Quay lại thư viện", close, "artifact-back ghost-button");
    closeButton.setAttribute("aria-label", "Đóng preview, quay lại nội dung"); header.append(name, closeButton);
    const controls = element("div", "artifact-preview-controls");
    const versions = element("select"); versions.setAttribute("aria-label", "Phiên bản file");
    const download = element("a", "ghost-button", "Tải xuống"); download.hidden = true;
    const more = button("Thêm phiên bản", () => { void loadVersions(true).catch(error => { status.textContent = error.message; }); }); more.hidden = true;
    controls.append(versions, more, download);
    const lineage = element("p", "document-hint"), status = element("p", "document-status"); status.setAttribute("role", "status");
    const origin = element("a", "text-link", "Mở cuộc trò chuyện gốc"); origin.hidden = true;
    const body = element("div", "artifact-preview-body");
    panel.append(header, controls, lineage, origin, status, body); root.append(panel);
    let generation = 0, controller = null, current = null, context = null, focused = null, versionOffset = 0;
    const pendingReady = new Map();
    const narrow = matchMedia("(max-width: 760px)");
    const primary = root.querySelector(".chat-main, .artifact-primary");
    function accessibility() {
      panel.setAttribute("role", narrow.matches ? "dialog" : "complementary");
      if (narrow.matches && !panel.hidden) panel.setAttribute("aria-modal", "true"); else panel.removeAttribute("aria-modal");
      if (primary) primary.inert = narrow.matches && !panel.hidden;
    }
    narrow.addEventListener("change", accessibility);
    function close() {
      ++generation; controller?.abort(); controller = null; panel.hidden = true; current = null; context = null;
      pendingReady.clear();
      root.classList.remove("artifact-preview-open"); accessibility();
      if (focused?.isConnected) focused.focus(); focused = null;
    }
    function columnName(column) { let text = ""; while (column) { column--; text = String.fromCharCode(65 + column % 26) + text; column = Math.floor(column / 26); } return text; }
    function render(data) {
      body.replaceChildren();
      if (data.preview.kind === "source_copy") { body.append(element("p", "document-hint", "Bản sao nguồn được giữ nguyên. Hiện chỉ có preview và chỉnh sửa Excel; bạn có thể tải file này xuống.")); return; }
      const preview = data.preview, sheets = element("div", "artifact-sheets");
      for (const sheet of preview.sheets || []) {
        const tab = button(sheet.name, () => { void open(data.version_id, context, { sheet: sheet.name }, false); });
        tab.setAttribute("aria-pressed", String(sheet.name === preview.sheet)); sheets.append(tab);
      }
      body.append(sheets);
      const inventory = (preview.sheets || []).find(sheet => sheet.name === preview.sheet);
      const wrapper = element("div", "artifact-grid-wrap"), table = element("table", "artifact-grid");
      table.setAttribute("aria-label", `Dữ liệu ${preview.sheet || "Excel"}`);
      const head = element("thead"), columns = element("tr"); columns.append(element("th", "", "Ô"));
      const width = Math.min(inventory?.columns || 1, 100);
      for (let col = 1; col <= width; col++) columns.append(element("th", "", columnName(col)));
      head.append(columns); table.append(head); const rows = element("tbody");
      const cells = new Map((preview.cells || []).map(cell => [`${cell.row}:${cell.column}`, cell]));
      const last = Math.min(inventory?.rows || 1, preview.offset + preview.limit);
      for (let row = preview.offset + 1; row <= last; row++) {
        const line = element("tr"); const label = element("th", "", String(row)); label.scope = "row"; line.append(label);
        for (let col = 1; col <= width; col++) {
          const cell = cells.get(`${row}:${col}`), value = element("td");
          if (cell) {
            value.textContent = cell.formula ? (cell.cached_value ?? "Chưa có kết quả") : (cell.value ?? "");
            if (cell.formula) { value.className = "has-formula"; value.title = cell.formula; value.append(element("small", "artifact-formula", cell.formula)); }
            if (cell.truncated) value.append(element("small", "document-hint", "Nội dung dài được rút gọn khi xem."));
          }
          line.append(value);
        }
        rows.append(line);
      }
      table.append(rows); wrapper.append(table); body.append(wrapper);
      const pagination = element("div", "document-pagination");
      const previous = button("Trước", () => { void open(data.version_id, context, { sheet: preview.sheet, offset: Math.max(0, preview.offset - preview.limit) }, false); }); previous.disabled = preview.offset === 0;
      const next = button("Sau", () => { void open(data.version_id, context, { sheet: preview.sheet, offset: preview.offset + preview.limit }, false); }); next.disabled = last >= (inventory?.rows || 0);
      pagination.append(previous, element("span", "", `${preview.offset + 1}–${last} / ${inventory?.rows || 1} hàng`), next); body.append(pagination);
      if (preview.columns_truncated) body.append(element("p", "document-hint", "Preview hiển thị tối đa 100 cột. Tải xuống để xem đầy đủ."));
      for (const chart of preview.charts || []) renderChart(chart, body, data.calculation_status);
      if (inventory?.charts && !preview.charts?.length) body.append(element("p", "document-hint", `${inventory.charts} biểu đồ được giữ trong file tải xuống.`));
    }
    function renderChart(chart, target, calculation) {
      const figure = element("figure", "artifact-chart"); figure.append(element("figcaption", "", chart.title || "Biểu đồ"));
      if (calculation !== "fresh") { figure.append(element("p", "document-hint", "Giá trị biểu đồ chưa được xác minh; xem file tải xuống.")); target.append(figure); return; }
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 480 200"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", chart.title || "Biểu đồ Excel");
      const colors = ["#ad5135", "#456b68", "#807445", "#745b81"];
      const series = chart.series || [], values = series.flatMap(item => item.values).filter(Number.isFinite);
      const low = Math.min(0, ...values), high = Math.max(1, ...values), scale = value => 175 - (value - low) / (high - low) * 150;
      series.forEach((item, index) => {
        const count = item.values.length;
        if (chart.type === "line") {
          const points = item.values.map((value, i) => Number.isFinite(value) ? `${30 + i * 420 / Math.max(1, count - 1)},${scale(value)}` : null).filter(Boolean).join(" ");
          const line = document.createElementNS(svg.namespaceURI, "polyline"); line.setAttribute("points", points); line.setAttribute("fill", "none"); line.setAttribute("stroke", colors[index % colors.length]); line.setAttribute("stroke-width", "2"); svg.append(line);
        } else item.values.forEach((value, i) => {
          if (!Number.isFinite(value)) return;
          const rect = document.createElementNS(svg.namespaceURI, "rect");
          const width = 420 / Math.max(1, count) / Math.max(1, series.length), baseline = scale(0);
          if (chart.type === "bar") {
            const x = v => 30 + (v - low) / (high - low) * 420, height = 150 / count / Math.max(1, series.length);
            rect.setAttribute("x", String(Math.min(x(0), x(value)))); rect.setAttribute("y", String(20 + i * 150 / count + index * height)); rect.setAttribute("width", String(Math.abs(x(value) - x(0)))); rect.setAttribute("height", String(Math.max(1, height - 2)));
          } else {
            rect.setAttribute("x", String(30 + i * 420 / count + index * width)); rect.setAttribute("y", String(Math.min(baseline, scale(value)))); rect.setAttribute("width", String(Math.max(1, width - 2))); rect.setAttribute("height", String(Math.abs(baseline - scale(value))));
          }
          rect.setAttribute("fill", colors[index % colors.length]); svg.append(rect);
        });
      });
      figure.append(svg);
      const summary = element("p", "document-hint", `${chart.categories?.join(" · ") || ""}${chart.truncated ? " · Biểu đồ rút gọn" : ""}`); figure.append(summary); target.append(figure);
    }
    async function loadVersions(append = false) {
      if (!current) return;
      const token = generation, artifact = current.artifact_id, selected = current.version_id;
      if (!append) { versions.replaceChildren(); versionOffset = 0; }
      const page = await requestJson(`/api/artifacts/${encodeURIComponent(artifact)}/versions?offset=${versionOffset}&limit=100`, { signal: controller.signal });
      if (token !== generation || panel.hidden) return;
      for (const ref of page.items) { if ([...versions.options].some(option => option.value === ref.version_id)) continue; const option = element("option", "", `v${ref.version_number} · ${ref.name}`); option.value = ref.version_id; versions.append(option); }
      if (![...versions.options].some(option => option.value === selected)) { const option = element("option", "", `v${current.version_number} · ${current.name}`); option.value = selected; versions.append(option); }
      versions.value = selected; versionOffset += page.items.length; more.hidden = versionOffset >= page.total;
    }
    async function open(versionId, contextId, query = {}, focus = true) {
      const token = ++generation; controller?.abort(); controller = new AbortController();
      pendingReady.clear();
      if (panel.hidden) focused = document.activeElement;
      panel.hidden = false; root.classList.add("artifact-preview-open"); context = contextId; current = null;
      accessibility(); if (focus) closeButton.focus();
      name.textContent = "Đang mở file…"; status.textContent = "Đang tải đúng phiên bản…"; body.replaceChildren(); download.hidden = true; origin.hidden = true; versions.disabled = true; more.hidden = true; lineage.textContent = "";
      try {
        const params = new URLSearchParams(query);
        const data = await requestJson(`/api/artifact-versions/${encodeURIComponent(versionId)}/preview?${params}`, { signal: controller.signal });
        if (token !== generation || panel.hidden || context !== contextId) return;
        const ready = pendingReady.get(data.artifact_id); pendingReady.clear();
        if (ready && ready.version_id !== data.version_id) { void open(ready.version_id, contextId, {}, false); return; }
        current = data; panel.dataset.versionId = data.version_id; name.textContent = data.name;
        status.textContent = statusText(data.calculation_status);
        lineage.textContent = `Phiên bản ${data.version_number}${data.parent_version_id ? ` · sửa từ ${data.parent_version_id.slice(0, 8)}` : " · bản đầu"}`;
        download.href = `/api/artifact-versions/${encodeURIComponent(data.version_id)}/download`; download.hidden = false;
        if (data.source_conversation_id) { origin.href = `/chat?conversation=${encodeURIComponent(data.source_conversation_id)}`; origin.hidden = false; }
        render(data); await loadVersions(); if (token === generation) versions.disabled = false;
      } catch (error) { if (token === generation && error.name !== "AbortError") { status.textContent = error.message; name.textContent = "Không thể mở file"; } }
    }
    versions.addEventListener("change", () => { void open(versions.value, context, {}, false); });
    panel.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key === "Tab" && narrow.matches) {
        const focusable = [...panel.querySelectorAll("button:not(:disabled),a[href],select:not(:disabled)")].filter(item => !item.hidden && item.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    });
    return { open, close, onVersionReady(ref, contextId) {
      if (panel.hidden || context !== contextId) return;
      if (!current) { pendingReady.set(ref.artifact_id, ref); return; }
      if (current.artifact_id === ref.artifact_id && current.version_id !== ref.version_id) void open(ref.version_id, contextId, {}, false);
    } };
  };
  window.renderArtifactCards = (bubble, refs, contextId) => {
    if (!refs?.length) return;
    const row = bubble.closest(".message-row"); if (!row) return;
    let list = row.querySelector(".artifact-cards"); if (!list) { list = element("div", "artifact-cards"); row.append(list); }
    for (const ref of refs) {
      if ([...list.children].some(item => item.dataset.versionId === ref.version_id)) continue;
      const card = element("div", "artifact-card"); card.dataset.versionId = ref.version_id;
      const open = button(ref.name, () => { void window.ArtifactWorkspace?.onOpen?.(ref.version_id, contextId); }, "artifact-card-open");
      open.append(element("small", "document-hint", `${Math.ceil(ref.size_bytes / 1024)} KB · ${statusText(ref.calculation_status)}`));
      const download = element("a", "text-link", "Tải xuống"); download.href = `/api/artifact-versions/${encodeURIComponent(ref.version_id)}/download`;
      card.append(open, download); list.append(card);
    }
  };
})();
