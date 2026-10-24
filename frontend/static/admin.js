const adminRoot = document.querySelector("[data-admin-dashboard]");

if (adminRoot) {
  const statusBox = adminRoot.querySelector("[data-admin-status]");
  const windowButtons = [...adminRoot.querySelectorAll("[data-admin-days]")];
  const refreshButton = adminRoot.querySelector("[data-admin-refresh]");
  const evalStatusBox = adminRoot.querySelector("[data-admin-eval-status]");
  const baselineSelect = adminRoot.querySelector("[data-admin-eval-baseline]");
  const candidateSelect = adminRoot.querySelector("[data-admin-eval-candidate]");
  const compareButton = adminRoot.querySelector("[data-admin-eval-compare]");
  const runDialog = document.querySelector("[data-admin-run-dialog]");
  const runDetailStatus = runDialog.querySelector("[data-run-detail-status]");
  const runDetailContent = runDialog.querySelector("[data-run-detail-content]");
  const alertPanel = adminRoot.querySelector("[data-admin-alert-panel]");
  const auditStatusBox = adminRoot.querySelector("[data-admin-audit-status]");
  const numberFormatter = new Intl.NumberFormat("vi-VN");
  const costFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 8,
  });
  let selectedDays = 7;
  let evalRuns = [];

  function setMetric(name, value) {
    adminRoot.querySelectorAll(`[data-admin-metric="${name}"]`).forEach((element) => {
      element.textContent = value;
    });
  }

  function formatNumber(value) {
    return numberFormatter.format(Number(value || 0));
  }

  function formatOptionalNumber(value) {
    if (value === null || value === undefined) return "—";
    return formatNumber(value);
  }

  function formatDuration(value) {
    if (value === null || value === undefined) return "—";
    if (value < 1000) return `${formatNumber(value)} ms`;
    return `${(value / 1000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} s`;
  }

  function formatTime(value) {
    return new Intl.DateTimeFormat("vi-VN", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(value));
  }

  function formatRate(value) {
    if (value === null || value === undefined) return "—";
    return `${(value * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`;
  }

  function formatCost(value) {
    return costFormatter.format(Number(value || 0));
  }

  function shortId(value) {
    return value ? value.slice(0, 8) : "—";
  }

  function formatRateDelta(value) {
    if (value === null || value === undefined) return "—";
    const points = value * 100;
    const prefix = points > 0 ? "+" : "";
    return `${prefix}${points.toLocaleString("vi-VN", { maximumFractionDigits: 1 })} điểm`;
  }

  function deltaClass(value) {
    if (value > 0) return "is-positive";
    if (value < 0) return "is-negative";
    return "is-neutral";
  }

  function appendCell(row, value, className = "") {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value;
    row.appendChild(cell);
  }

  function appendStatusCell(row, status) {
    const cell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `admin-status-badge status-${status}`;
    badge.textContent = status;
    cell.appendChild(badge);
    row.appendChild(cell);
  }

  function renderModelCalls(items) {
    const body = adminRoot.querySelector("[data-admin-model-calls]");
    body.replaceChildren();
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.className = "admin-empty-cell";
      cell.textContent = "Chưa có model call trong khoảng thời gian này.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, item.model_name, "admin-primary-cell");
      appendStatusCell(row, item.status);
      appendCell(row, formatNumber(item.total_tokens));
      appendCell(row, formatDuration(item.latency_ms));
      appendCell(row, item.prompt_version || "—");
      appendCell(row, formatTime(item.created_at));
      body.appendChild(row);
    });
  }

  function renderToolCalls(items) {
    const body = adminRoot.querySelector("[data-admin-tool-calls]");
    body.replaceChildren();
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "admin-empty-cell";
      cell.textContent = "Chưa có tool call trong khoảng thời gian này.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, item.tool_name, "admin-primary-cell");
      appendStatusCell(row, item.status);
      appendCell(row, formatDuration(item.duration_ms));
      appendCell(row, formatTime(item.created_at));
      body.appendChild(row);
    });
  }

  function renderErrors(items) {
    const list = adminRoot.querySelector("[data-admin-errors]");
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "admin-error-empty";
      empty.textContent = "Không ghi nhận lỗi trong khoảng thời gian này.";
      list.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "admin-error-item";
      const top = document.createElement("div");
      const source = document.createElement("strong");
      const time = document.createElement("time");
      source.textContent = `${item.source} · ${item.code}`;
      time.textContent = formatTime(item.created_at);
      top.append(source, time);
      const message = document.createElement("p");
      message.textContent = item.message;
      card.append(top, message);
      list.appendChild(card);
    });
  }

  function renderRunStatuses(statuses) {
    const list = adminRoot.querySelector("[data-admin-run-statuses]");
    list.replaceChildren();
    Object.entries(statuses).forEach(([name, count]) => {
      const item = document.createElement("div");
      const label = document.createElement("span");
      const value = document.createElement("strong");
      label.textContent = name.replace("_", " ");
      value.textContent = formatNumber(count);
      item.append(label, value);
      list.appendChild(item);
    });
  }

  function renderAlerts(alerts) {
    const list = adminRoot.querySelector("[data-admin-alerts]");
    list.replaceChildren();
    alertPanel.hidden = false;
    adminRoot.querySelector("[data-admin-alert-count]").textContent = formatNumber(alerts.length);
    if (!alerts.length) {
      const healthy = document.createElement("p");
      healthy.className = "admin-alert-empty";
      healthy.textContent = "Không có chỉ số nào vượt ngưỡng cảnh báo.";
      list.appendChild(healthy);
      return;
    }
    alerts.forEach((alert) => {
      const card = document.createElement("article");
      card.className = `admin-alert-item severity-${alert.severity}`;
      const heading = document.createElement("div");
      const title = document.createElement("strong");
      const severity = document.createElement("span");
      title.textContent = alert.title;
      severity.textContent = alert.severity;
      heading.append(title, severity);
      const detail = document.createElement("p");
      detail.textContent = alert.detail;
      card.append(heading, detail);
      list.appendChild(card);
    });
  }

  function renderAuditLogs(items) {
    const body = adminRoot.querySelector("[data-admin-audit-logs]");
    body.replaceChildren();
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "admin-empty-cell";
      cell.textContent = "Chưa có audit event.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, formatTime(item.created_at));
      appendCell(row, item.actor_user_id ? shortId(item.actor_user_id) : "system", "admin-mono-cell");
      appendCell(row, item.action, "admin-primary-cell");
      appendCell(row, item.target_type ? `${item.target_type}:${shortId(item.target_id)}` : "—");
      appendStatusCell(row, item.outcome);
      body.appendChild(row);
    });
  }

  async function loadAuditLogs() {
    auditStatusBox.classList.remove("is-error");
    try {
      const response = await fetch("/api/admin/audit-logs?limit=20", {
        credentials: "same-origin",
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Không thể tải audit log.");
      renderAuditLogs(result.logs);
      auditStatusBox.textContent = `${formatNumber(result.logs.length)} audit event gần nhất.`;
    } catch (error) {
      renderAuditLogs([]);
      auditStatusBox.classList.add("is-error");
      auditStatusBox.textContent = error.message || "Không thể tải audit log.";
    }
  }

  function renderAgentRuns(items) {
    const body = adminRoot.querySelector("[data-admin-agent-runs]");
    body.replaceChildren();
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.className = "admin-empty-cell";
      cell.textContent = "Chưa có agent run trong khoảng thời gian này.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, shortId(item.id), "admin-primary-cell admin-mono-cell");
      appendStatusCell(row, item.status);
      appendCell(row, formatNumber(item.current_step));
      appendCell(row, formatDuration(item.duration_ms));
      appendCell(row, item.stop_reason || "—");
      appendCell(row, formatTime(item.created_at));
      const actionCell = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "run-detail-button";
      button.textContent = "Chi tiết";
      button.addEventListener("click", () => loadAgentRunDetail(item.id));
      actionCell.appendChild(button);
      row.appendChild(actionCell);
      body.appendChild(row);
    });
  }

  function detailMetric(label, value) {
    const item = document.createElement("div");
    const name = document.createElement("span");
    const metric = document.createElement("strong");
    name.textContent = label;
    metric.textContent = value;
    item.append(name, metric);
    return item;
  }

  function renderAgentRunDetail(data) {
    runDialog.querySelector("[data-run-detail-title]").textContent = `Run ${shortId(data.id)}`;
    const metrics = runDialog.querySelector("[data-run-detail-metrics]");
    metrics.replaceChildren(
      detailMetric("Trạng thái", data.status),
      detailMetric("Token", formatNumber(data.total_tokens)),
      detailMetric("Cache hit", formatRate(data.cache_hit_rate)),
      detailMetric("Chi phí", formatCost(data.estimated_cost_usd)),
      detailMetric("Thời gian", formatDuration(data.duration_ms)),
      detailMetric("Resume", formatNumber(data.resume_count)),
    );

    const meta = runDialog.querySelector("[data-run-detail-meta]");
    meta.replaceChildren();
    const metaItems = [
      `Context: ${formatOptionalNumber(data.context_total_tokens)} / ${formatOptionalNumber(data.context_budget_tokens)} token`,
      `Message bị trim: ${formatNumber(data.context_trimmed_messages)}`,
      `Tool selection: ${data.tool_selection_mode || "—"} (${formatOptionalNumber(data.loaded_tool_count)}/${formatOptionalNumber(data.available_tool_count)})`,
      `Stop reason: ${data.stop_reason || "—"}`,
      `Bắt đầu: ${formatTime(data.created_at)}`,
    ];
    metaItems.forEach((text) => {
      const item = document.createElement("span");
      item.textContent = text;
      meta.appendChild(item);
    });

    const stepList = runDialog.querySelector("[data-run-detail-steps]");
    stepList.replaceChildren();
    if (!data.steps.length) {
      const empty = document.createElement("p");
      empty.className = "admin-empty-cell";
      empty.textContent = "Run chưa có step.";
      stepList.appendChild(empty);
    }
    data.steps.forEach((step) => {
      const card = document.createElement("article");
      card.className = "run-step-card";
      const heading = document.createElement("div");
      const title = document.createElement("strong");
      const status = document.createElement("span");
      title.textContent = `Step ${step.step_number} · attempt ${step.attempt_number}`;
      status.className = `admin-status-badge status-${step.status}`;
      status.textContent = `${step.status} · ${formatDuration(step.duration_ms)}`;
      heading.append(title, status);
      card.appendChild(heading);

      if (step.model_call) {
        const model = document.createElement("p");
        model.className = "run-step-model";
        model.textContent = `${step.model_call.model_name} · ${formatNumber(step.model_call.total_tokens)} token · cache ${formatRate(step.model_call.cache_hit_rate)} · ${formatDuration(step.model_call.latency_ms)} · ${formatCost(step.model_call.estimated_cost_usd)}`;
        card.appendChild(model);
      }
      if (step.tool_calls.length) {
        const tools = document.createElement("div");
        tools.className = "run-step-tools";
        step.tool_calls.forEach((tool) => {
          const toolItem = document.createElement("span");
          toolItem.textContent = `${tool.tool_name} · ${tool.status} · ${formatDuration(tool.duration_ms)}${tool.error_code ? ` · ${tool.error_code}` : ""}`;
          tools.appendChild(toolItem);
        });
        card.appendChild(tools);
      }
      stepList.appendChild(card);
    });
    runDetailStatus.textContent = "";
    runDetailContent.hidden = false;
  }

  async function loadAgentRunDetail(runId) {
    runDetailContent.hidden = true;
    runDetailStatus.classList.remove("is-error");
    runDetailStatus.textContent = "Đang tải chi tiết run…";
    if (!runDialog.open) runDialog.showModal();
    try {
      const response = await fetch(`/api/admin/agent-runs/${runId}`, {
        credentials: "same-origin",
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Không thể tải chi tiết agent run.");
      renderAgentRunDetail(result);
      loadAuditLogs();
    } catch (error) {
      runDetailStatus.classList.add("is-error");
      runDetailStatus.textContent = error.message || "Không thể tải chi tiết agent run.";
    }
  }

  async function loadAgentRuns() {
    try {
      const response = await fetch(`/api/admin/agent-runs?days=${selectedDays}&limit=20`, {
        credentials: "same-origin",
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Không thể tải agent runs.");
      renderAgentRuns(result.runs);
    } catch (error) {
      renderAgentRuns([]);
      statusBox.classList.add("is-error");
      statusBox.textContent = error.message || "Không thể tải agent runs.";
    }
  }

  function evalRunLabel(run) {
    return `${formatTime(run.created_at)} · ${run.model_name} · ${run.prompt_version}`;
  }

  function fillEvalSelect(select, runs, selectedId) {
    select.replaceChildren();
    if (!runs.length) {
      const option = document.createElement("option");
      option.textContent = "Chưa có eval run";
      option.value = "";
      select.appendChild(option);
      return;
    }
    runs.forEach((run) => {
      const option = document.createElement("option");
      option.value = run.id;
      option.textContent = evalRunLabel(run);
      option.selected = run.id === selectedId;
      select.appendChild(option);
    });
  }

  function renderEvalRuns(runs) {
    const body = adminRoot.querySelector("[data-admin-eval-runs]");
    body.replaceChildren();
    if (!runs.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 8;
      cell.className = "admin-empty-cell";
      cell.textContent = "Chưa có eval run. Hãy chạy scripts.run_evals để tạo dữ liệu.";
      row.appendChild(cell);
      body.appendChild(row);
      fillEvalSelect(baselineSelect, [], "");
      fillEvalSelect(candidateSelect, [], "");
      compareButton.disabled = true;
      return;
    }
    runs.forEach((run) => {
      const row = document.createElement("tr");
      appendCell(row, formatTime(run.created_at));
      appendCell(row, `${run.dataset_id}@${run.dataset_version}`, "admin-primary-cell");
      appendCell(row, run.model_name);
      appendCell(row, run.prompt_version);
      appendCell(row, formatRate(run.pass_rate), "admin-rate-cell");
      appendCell(row, formatRate(run.tool_success_rate));
      appendCell(row, `${run.completed_cases}/${run.total_cases}`);
      appendStatusCell(row, run.status);
      body.appendChild(row);
    });
    const baselineId = runs.length > 1 ? runs[1].id : runs[0].id;
    const candidateId = runs[0].id;
    fillEvalSelect(baselineSelect, runs, baselineId);
    fillEvalSelect(candidateSelect, runs, candidateId);
    compareButton.disabled = runs.length < 2;
  }

  function setEvalDelta(name, value) {
    const element = adminRoot.querySelector(`[data-eval-value="${name}"]`);
    element.textContent = formatRateDelta(value);
    element.className = deltaClass(value);
  }

  function renderEvalComparison(data) {
    const panel = adminRoot.querySelector("[data-admin-eval-comparison]");
    panel.hidden = false;
    adminRoot.querySelector("[data-eval-value='baseline_pass']").textContent = formatRate(data.baseline.pass_rate);
    adminRoot.querySelector("[data-eval-value='candidate_pass']").textContent = formatRate(data.candidate.pass_rate);
    setEvalDelta("pass_delta", data.delta.pass_rate);
    setEvalDelta("tool_delta", data.delta.tool_success_rate);

    const categories = adminRoot.querySelector("[data-admin-eval-categories]");
    categories.replaceChildren();
    Object.entries(data.delta.categories).forEach(([name, delta]) => {
      const row = document.createElement("div");
      const label = document.createElement("strong");
      const baseline = document.createElement("span");
      const candidate = document.createElement("span");
      const change = document.createElement("span");
      label.textContent = name;
      baseline.textContent = formatRate(data.baseline.categories[name]?.pass_rate);
      candidate.textContent = formatRate(data.candidate.categories[name]?.pass_rate);
      change.textContent = formatRateDelta(delta);
      change.className = deltaClass(delta);
      row.append(label, baseline, candidate, change);
      categories.appendChild(row);
    });

    const failures = adminRoot.querySelector("[data-admin-eval-failures]");
    failures.replaceChildren();
    const failureEntries = Object.entries(data.delta.failure_categories);
    if (!failureEntries.length) {
      const empty = document.createElement("p");
      empty.className = "eval-comparison-empty";
      empty.textContent = "Hai run không có failure category.";
      failures.appendChild(empty);
    } else {
      failureEntries.forEach(([name, delta]) => {
        const row = document.createElement("div");
        const label = document.createElement("span");
        const change = document.createElement("strong");
        label.textContent = name.replaceAll("_", " ");
        change.textContent = `${delta > 0 ? "+" : ""}${delta}`;
        change.className = deltaClass(-delta);
        row.append(label, change);
        failures.appendChild(row);
      });
    }

    const warnings = adminRoot.querySelector("[data-admin-eval-warnings]");
    warnings.replaceChildren();
    data.warnings.forEach((text) => {
      const item = document.createElement("p");
      item.textContent = text;
      warnings.appendChild(item);
    });
  }

  async function loadEvalComparison() {
    const baselineId = baselineSelect.value;
    const candidateId = candidateSelect.value;
    if (!baselineId || !candidateId || baselineId === candidateId) {
      evalStatusBox.classList.add("is-error");
      evalStatusBox.textContent = "Hãy chọn hai eval run khác nhau để so sánh.";
      return;
    }
    compareButton.disabled = true;
    evalStatusBox.classList.remove("is-error");
    evalStatusBox.textContent = "Đang so sánh hai phiên bản…";
    try {
      const params = new URLSearchParams({
        baseline_id: baselineId,
        candidate_id: candidateId,
      });
      const response = await fetch(`/api/admin/evals/compare?${params}`, {
        credentials: "same-origin",
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Không thể so sánh eval run.");
      renderEvalComparison(result);
      loadAuditLogs();
      evalStatusBox.textContent = result.comparable
        ? "Hai run dùng cùng dataset và tập case; có thể so sánh trực tiếp."
        : "Hai run không hoàn toàn tương đương; hãy đọc cảnh báo trước khi kết luận.";
    } catch (error) {
      evalStatusBox.classList.add("is-error");
      evalStatusBox.textContent = error.message || "Không thể so sánh eval run.";
    } finally {
      compareButton.disabled = evalRuns.length < 2;
    }
  }

  async function loadEvalRuns() {
    evalStatusBox.classList.remove("is-error");
    evalStatusBox.textContent = "Đang tải eval runs…";
    try {
      const response = await fetch("/api/admin/evals?limit=20", {
        credentials: "same-origin",
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Không thể tải eval runs.");
      evalRuns = result.runs;
      renderEvalRuns(evalRuns);
      if (evalRuns.length >= 2) {
        await loadEvalComparison();
      } else {
        evalStatusBox.textContent = evalRuns.length
          ? "Cần ít nhất hai eval run để so sánh phiên bản."
          : "Chưa có eval run. Chạy scripts.run_evals để bắt đầu.";
      }
    } catch (error) {
      evalStatusBox.classList.add("is-error");
      evalStatusBox.textContent = error.message || "Không thể tải eval runs.";
    }
  }

  function render(data) {
    setMetric("total_tokens", formatNumber(data.tokens.total));
    setMetric("prompt_tokens", formatNumber(data.tokens.prompt));
    setMetric("prompt_tokens_detail", formatNumber(data.tokens.prompt));
    setMetric("completion_tokens", formatNumber(data.tokens.completion));
    setMetric("completion_tokens_detail", formatNumber(data.tokens.completion));
    setMetric("cached_tokens", formatNumber(data.tokens.cached_input));
    setMetric("cache_miss_tokens", formatNumber(data.tokens.cache_miss_input));
    setMetric("cache_hit_rate", formatRate(data.cache.hit_rate));
    setMetric("estimated_cost", formatCost(data.cost.estimated_total));
    setMetric("cached_cost", formatCost(data.cost.cached_input));
    setMetric("cache_miss_cost", formatCost(data.cost.cache_miss_input));
    setMetric("output_cost", formatCost(data.cost.output));
    setMetric("model_turns", formatNumber(data.activity.model_turns));
    setMetric("agent_runs", formatNumber(data.activity.agent_runs));
    setMetric("model_calls", formatNumber(data.activity.model_calls));
    setMetric("tool_calls", formatNumber(data.activity.tool_calls));
    setMetric("tool_completed", formatNumber(data.tools.completed));
    setMetric("tool_denied", formatNumber(data.tools.denied));
    setMetric("model_latency", formatDuration(data.latency.average_model_ms));
    setMetric("tool_latency", formatDuration(data.latency.average_tool_ms));
    setMetric("model_p50", formatDuration(data.latency.model_p50_ms));
    setMetric("model_p95", formatDuration(data.latency.model_p95_ms));
    setMetric("tool_p50", formatDuration(data.latency.tool_p50_ms));
    setMetric("tool_p95", formatDuration(data.latency.tool_p95_ms));
    setMetric("errors", formatNumber(data.errors.total));
    adminRoot.querySelector("[data-admin-window-label]").textContent = `${data.window_days} ngày`;
    renderRunStatuses(data.run_statuses);
    renderModelCalls(data.recent_model_calls);
    renderToolCalls(data.recent_tool_calls);
    renderErrors(data.recent_errors);
    renderAlerts(data.alerts);
  }

  async function loadRouting() {
    const summary = adminRoot.querySelector("[data-routing-summary]");
    const metrics = adminRoot.querySelector("[data-routing-metrics]");
    const body = adminRoot.querySelector("[data-routing-traces]");
    if (!summary || !metrics || !body) return;
    try {
      const response = await fetch(`/api/admin/routing?days=${selectedDays}`, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Không thể tải routing; kiểm tra migration và quyền admin.");
      const data = await response.json();
      summary.textContent = `${data.mode} · circuit ${data.circuit.state}`;
      metrics.textContent = `${data.decisions} quyết định · ${data.fallback_decisions} fallback · ` +
        `${data.shadow_decisions} shadow · ${data.safe_stops} dừng an toàn · ` +
        `input ${data.input_tokens ?? "chưa rõ"} / output ${data.output_tokens ?? "chưa rõ"} token · ` +
        `${data.unknown_usage_decisions} lần thiếu usage · P95 ${formatDuration(data.p95_latency_ms)}`;
      body.replaceChildren();
      for (const trace of data.recent) {
        const row = document.createElement("tr");
        const values = [
          `${trace.run_id.slice(0, 8)} / ${trace.phase}`,
          trace.mode,
          `${trace.provider} / ${trace.model_version ?? "—"}`,
          `${trace.status}: ${trace.selected_id ?? "—"}`,
          `${trace.input_tokens ?? "?"} / ${trace.output_tokens ?? "?"}`,
          formatDuration(trace.latency_ms),
          trace.fallback_reason ?? "—",
        ];
        for (const value of values) {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.appendChild(cell);
        }
        body.appendChild(row);
      }
    } catch (error) {
      summary.textContent = error.message;
      metrics.textContent = "";
      body.replaceChildren();
    }
  }

  async function loadOverview() {
    statusBox.classList.remove("is-error");
    statusBox.textContent = "Đang cập nhật dữ liệu vận hành…";
    refreshButton.disabled = true;
    try {
      const response = await fetch(`/api/admin/overview?days=${selectedDays}`, {
        credentials: "same-origin",
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Không thể tải dashboard admin.");
      }
      const data = await response.json();
      render(data);
      await loadRouting();
      statusBox.textContent = `Dữ liệu từ ${formatTime(data.from_time)} đến ${formatTime(data.to_time)}.`;
    } catch (error) {
      statusBox.classList.add("is-error");
      statusBox.textContent = error.message || "Không thể tải dashboard admin.";
    } finally {
      refreshButton.disabled = false;
    }
  }

  windowButtons.forEach((button) => {
    button.addEventListener("click", () => {
      selectedDays = Number(button.dataset.adminDays);
      windowButtons.forEach((item) => item.classList.toggle("is-active", item === button));
      loadOverview();
      loadAgentRuns();
    });
  });
  compareButton.addEventListener("click", loadEvalComparison);
  refreshButton.addEventListener("click", () => {
    loadOverview();
    loadAgentRuns();
    loadEvalRuns();
    loadAuditLogs();
  });
  runDialog.querySelector("[data-run-detail-close]").addEventListener("click", () => {
    runDialog.close();
  });
  loadOverview();
  loadAgentRuns();
  loadEvalRuns();
  loadAuditLogs();
}
