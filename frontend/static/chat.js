const chatApp = document.querySelector("[data-chat-app]");

if (chatApp) {
  const conversationList = chatApp.querySelector("[data-conversation-list]");
  const messageList = chatApp.querySelector("[data-message-list]");
  const messageForm = chatApp.querySelector("[data-message-form]");
  const textarea = messageForm.querySelector("textarea");
  const sendButton = messageForm.querySelector("button");
  const ragSearchMode = chatApp.querySelector("[data-rag-search-mode]");
  const title = chatApp.querySelector("[data-chat-title]");
  const status = chatApp.querySelector("[data-chat-status]");
  let activeConversationId = null;
  let conversationMutationPending = false;
  let streamInProgress = false;
  let documentMutationPending = false;
  let documentPanel = null;

  function updateConversationControls() {
    const disabled = streamInProgress || conversationMutationPending || documentMutationPending;
    textarea.disabled = disabled;
    sendButton.disabled = disabled;
    if (ragSearchMode) ragSearchMode.disabled = disabled;
    chatApp.querySelector("[data-new-chat]").disabled = disabled;
    conversationList.querySelectorAll("button").forEach((button) => {
      button.disabled = disabled;
    });
    documentPanel?.setDisabled(streamInProgress || conversationMutationPending);
  }

  function setBusy(isBusy) {
    streamInProgress = isBusy;
    updateConversationControls();
    if (isBusy) status.textContent = "Agent đang bắt đầu xử lý…";
  }

  function showError(message) {
    status.textContent = message;
    status.classList.add("is-error");
  }

  function clearError() {
    status.classList.remove("is-error");
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 401) {
      window.location.assign("/login");
      throw new Error("Phiên đăng nhập đã hết hạn.");
    }
    const result = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể xử lý yêu cầu.");
    return result;
  }

  function renderConversationItems(conversations) {
    const scrollTop = conversationList.scrollTop;
    conversationList.replaceChildren();
    for (const conversation of conversations) {
      const row = document.createElement("div");
      row.className = "conversation-row";
      row.dataset.id = conversation.id;
      row.classList.toggle("is-active", conversation.id === activeConversationId);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-item";
      button.dataset.id = conversation.id;
      button.textContent = conversation.title;
      button.title = conversation.title;
      button.setAttribute("aria-current", conversation.id === activeConversationId ? "true" : "false");
      if (conversation.id === activeConversationId) button.classList.add("is-active");
      button.addEventListener("click", () => {
        selectConversation(conversation).catch((error) => showError(error.message));
      });
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-delete";
      deleteButton.title = "Xóa hội thoại";
      deleteButton.setAttribute("aria-label", `Xóa hội thoại ${conversation.title}`);
      deleteButton.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg>';
      deleteButton.addEventListener("click", () => deleteConversation(conversation));
      row.append(button, deleteButton);
      conversationList.append(row);
    }
    conversationList.scrollTop = scrollTop;
    updateConversationControls();
  }

  async function deleteConversation(conversation) {
    if (sendButton.disabled || conversationMutationPending) return;
    if (!window.confirm(`Xóa hội thoại “${conversation.title}” và lịch sử của nó?`)) return;
    clearError();
    conversationMutationPending = true;
    updateConversationControls();
    try {
      await requestJson(`/api/conversations/${conversation.id}`, { method: "DELETE" });
      if (activeConversationId === conversation.id) {
        activeConversationId = null;
        await loadConversations();
      } else {
        await refreshConversationList();
      }
      status.textContent = "Đã xóa hội thoại.";
    } catch (error) {
      showError(error.message || "Không thể xóa hội thoại.");
    } finally {
      conversationMutationPending = false;
      updateConversationControls();
      textarea.focus();
    }
  }

  function addMessage(role, content = "") {
    messageList.querySelector("[data-empty-chat]")?.remove();
    const row = document.createElement("article");
    row.className = `message-row ${role}`;
    const label = document.createElement("span");
    label.className = "message-author";
    label.textContent = role === "user" ? "Bạn" : "Agent";
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    if (role === "assistant") {
      bubble.classList.add("markdown-content");
      window.renderMarkdown(bubble, content);
    } else {
      bubble.textContent = content;
    }
    row.append(label, bubble);
    messageList.append(row);
    messageList.scrollTop = messageList.scrollHeight;
    return bubble;
  }

  const sourceDialog = document.createElement("dialog");
  const deletedDocumentIds = new Set();
  sourceDialog.className = "citation-dialog";
  sourceDialog.setAttribute("aria-label", "Nguồn tài liệu");
  document.body.append(sourceDialog);
  let sourceRequest = 0;
  sourceDialog.addEventListener("close", () => {
    sourceRequest += 1;
    sourceDialog.replaceChildren();
    delete sourceDialog.dataset.documentId;
  });

  async function openSource(conversationId, messageId, citationId, documentId) {
    if (deletedDocumentIds.has(documentId)) return;
    const requestId = ++sourceRequest;
    sourceDialog.dataset.documentId = documentId;
    sourceDialog.replaceChildren();
    const close = document.createElement("button");
    close.type = "button";
    close.className = "ghost-button citation-close";
    close.textContent = "Đóng";
    close.addEventListener("click", () => sourceDialog.close());
    const heading = document.createElement("h2");
    heading.textContent = `[${citationId}] Đang tải nguồn…`;
    const details = document.createElement("p");
    const excerpt = document.createElement("pre");
    sourceDialog.append(close, heading, details, excerpt);
    if (!sourceDialog.open) sourceDialog.showModal();
    try {
      const source = await requestJson(
        `/api/conversations/${encodeURIComponent(conversationId)}/messages/` +
        `${encodeURIComponent(messageId)}/citations/${encodeURIComponent(citationId)}`,
      );
      if (requestId !== sourceRequest || !sourceDialog.open) return;
      heading.textContent = `[${source.citation_id}] ${source.title}`;
      details.textContent = [
        source.page_start ? `Trang ${source.page_start}${source.page_end > source.page_start ? `–${source.page_end}` : ""}` : "",
        source.section || "",
      ].filter(Boolean).join(" · ");
      excerpt.textContent = source.text;
      sourceDialog.dataset.documentId = source.document_id;
      const download = document.createElement("a");
      download.className = "ghost-button";
      download.textContent = "Tải file gốc";
      download.href = `/api/documents/${encodeURIComponent(source.document_id)}/download` +
        `?version_id=${encodeURIComponent(source.version_id)}`;
      sourceDialog.append(download);
    } catch (error) {
      if (requestId !== sourceRequest || !sourceDialog.open) return;
      heading.textContent = "Không mở được nguồn";
      details.textContent = error.message;
    }
  }

  function renderCitations(bubble, citations, messageId, conversationId) {
    const valid = (citations || []).filter((source) =>
      /^S\d+$/.test(source.citation_id) && !deletedDocumentIds.has(source.document_id));
    bubble.parentElement.querySelector(".message-citations")?.remove();
    if (!valid.length || !messageId) return;
    const byLabel = new Map(valid.map((source) => [source.citation_id, source]));
    function citationButton(label, full = false) {
      const source = byLabel.get(label);
      const button = document.createElement("button");
      button.type = "button";
      button.className = full ? "citation-source" : "citation-inline";
      button.textContent = full
        ? `[${label}] ${source.title}${source.page_start ? ` · tr. ${source.page_start}` : ""}`
        : `[${label}]`;
      button.title = `Mở nguồn: ${source.title}`;
      button.dataset.citationDocumentId = source.document_id;
      button.addEventListener("click", () => openSource(conversationId, messageId, label, source.document_id));
      return button;
    }
    const nodes = [];
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest("a,button,code,pre") && /\[S\d+\]/.test(node.textContent)) nodes.push(node);
    }
    for (const node of nodes) {
      const fragment = document.createDocumentFragment();
      let offset = 0;
      for (const match of node.textContent.matchAll(/\[(S\d+)\]/g)) {
        fragment.append(document.createTextNode(node.textContent.slice(offset, match.index)));
        fragment.append(byLabel.has(match[1]) ? citationButton(match[1]) : document.createTextNode(match[0]));
        offset = match.index + match[0].length;
      }
      fragment.append(document.createTextNode(node.textContent.slice(offset)));
      node.replaceWith(fragment);
    }
    const panel = document.createElement("section");
    panel.className = "message-citations";
    panel.setAttribute("aria-label", "Nguồn tham khảo");
    const label = document.createElement("strong");
    label.textContent = "Nguồn tham khảo";
    panel.append(label);
    for (const source of valid) panel.append(citationButton(source.citation_id, true));
    bubble.after(panel);
  }

  window.addEventListener("document-library-changed", (event) => {
    if (event.detail?.type !== "delete") return;
    const documentId = event.detail.documentId;
    deletedDocumentIds.add(documentId);
    if (sourceDialog.open && sourceDialog.dataset.documentId === documentId) {
      sourceDialog.close();
    }
    messageList.querySelectorAll("[data-citation-document-id]").forEach((button) => {
      if (button.dataset.citationDocumentId !== documentId) return;
      if (button.classList.contains("citation-inline")) {
        button.replaceWith(document.createTextNode(`${button.textContent} (nguồn đã xóa)`));
      } else {
        const panel = button.parentElement;
        button.remove();
        if (!panel.querySelector("button")) panel.remove();
      }
    });
  });

  function createAgentTimeline(assistantBubble, expanded = true) {
    const root = document.createElement("details");
    root.className = "agent-timeline is-running";
    root.open = expanded;

    const summary = document.createElement("summary");
    const indicator = document.createElement("span");
    indicator.className = "timeline-indicator";
    indicator.setAttribute("aria-hidden", "true");
    const titleText = document.createElement("span");
    titleText.className = "timeline-title";
    titleText.textContent = "Tiến trình Agent";
    const count = document.createElement("span");
    count.className = "timeline-count";
    count.textContent = "Đang bắt đầu";
    const timer = document.createElement("span");
    timer.className = "timeline-timer";
    timer.textContent = "00:00";
    timer.setAttribute("aria-label", "Thời gian xử lý");
    summary.append(indicator, titleText, timer, count);

    const list = document.createElement("div");
    list.className = "timeline-list";
    list.setAttribute("role", "list");
    const statusBlock = document.createElement("section");
    statusBlock.className = "agent-status-block";
    statusBlock.hidden = true;
    const progressList = document.createElement("div");
    progressList.className = "pipeline-progress";
    progressList.setAttribute("role", "list");
    progressList.setAttribute("aria-label", "Các công đoạn xử lý");
    const searchMode = document.createElement("p");
    searchMode.className = "timeline-search-mode";
    searchMode.hidden = true;
    root.append(summary, searchMode, statusBlock, progressList, list);
    assistantBubble.before(root);

    const timeline = {
      root, titleText, count, timer, statusBlock, list, progressList, searchMode,
      items: new Map(), progressItems: new Map(), clock: null, poll: null, terminal: false,
    };
    if (expanded) startTimelineClock(timeline);
    return timeline;
  }

  function formatElapsed(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function updateTimelineSearchMode(timeline, mode) {
    timeline.searchMode.hidden = !["fast", "deep"].includes(mode);
    timeline.searchMode.textContent = mode === "deep"
      ? "Tìm kiếm tài liệu: Deep" : "Tìm kiếm tài liệu: Fast";
  }

  function tickTimelineClock(timeline) {
    const elapsed = timeline.elapsedBase + performance.now() - timeline.clockStarted;
    timeline.timer.textContent = formatElapsed(elapsed);
    for (const row of timeline.progressItems.values()) {
      if (row.state === "running") {
        row.time.textContent = `${formatElapsed(performance.now() - row.clockStarted)} · Đang chạy`;
      }
    }
  }

  function startTimelineClock(timeline, elapsed = 0) {
    stopTimelineClock(timeline);
    timeline.elapsedBase = Math.max(0, elapsed);
    timeline.clockStarted = performance.now();
    timeline.clock = window.setInterval(() => {
      if (!timeline.root.isConnected) return stopTimelineClock(timeline);
      tickTimelineClock(timeline);
    }, 1000);
    tickTimelineClock(timeline);
  }

  function stopTimelineClock(timeline) {
    if (timeline.clock !== null) {
      tickTimelineClock(timeline);
      window.clearInterval(timeline.clock);
      timeline.clock = null;
    }
  }

  function updatePipelineProgress(timeline, data) {
    if (!data?.id || !["running", "completed", "failed", "cancelled"].includes(data.state)) return;
    let row = timeline.progressItems.get(data.id);
    if (!row) {
      const root = document.createElement("div");
      root.className = "pipeline-stage";
      root.setAttribute("role", "listitem");
      const icon = document.createElement("span");
      icon.className = "pipeline-stage-icon";
      icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "pipeline-stage-label";
      const time = document.createElement("span");
      time.className = "pipeline-stage-time";
      root.append(icon, label, time);
      timeline.progressList.append(root);
      row = { root, icon, label, time };
      timeline.progressItems.set(data.id, row);
    }
    row.state = data.state;
    row.root.classList.toggle("is-substage", data.id.endsWith("_model") || data.id.endsWith(".citations"));
    row.clockStarted = performance.now() - (data.duration_ms || 0);
    row.root.dataset.state = data.state;
    row.icon.textContent = { running: "●", completed: "✓", failed: "×", cancelled: "–" }[data.state];
    const count = Number.isInteger(data.count) ? ` · ${data.count} đoạn` : "";
    row.label.textContent = `${data.label}${count}`;
    const duration = data.state === "running"
      ? formatElapsed(data.duration_ms || 0) : formatDuration(data.duration_ms || 0);
    row.time.textContent = `${duration}${
      data.state === "running" ? " · Đang chạy" : data.state === "failed" ? " · Lỗi"
        : data.state === "cancelled" ? " · Đã dừng" : ""
    }`;
    if (data.state === "running") timeline.titleText.textContent = data.label;
  }

  function formatDuration(durationMs) {
    if (durationMs === undefined || durationMs === null) return "";
    if (durationMs < 1000) return `${durationMs} ms`;
    return `${(durationMs / 1000).toFixed(1)} s`;
  }

  function setTimelineItemState(item, state) {
    item.classList.remove("is-running", "is-completed", "is-failed");
    item.classList.add(`is-${state}`);
  }

  function updateAgentStatusBlock(timeline, statusData) {
    if (!statusData) return;
    const budget = statusData.budget;
    const goal = document.createElement("p");
    goal.className = "status-goal";
    goal.textContent = `Mục tiêu: ${statusData.goal}`;
    const progress = document.createElement("p");
    progress.className = "status-progress";
    progress.textContent = `Tiến độ: ${statusData.progress.join(" ")}`;
    const remaining = document.createElement("p");
    remaining.className = "status-remaining";
    remaining.textContent = `Còn lại: ${statusData.remaining_work.join(" ")}`;
    const budgetView = document.createElement("p");
    budgetView.className = "status-budget";
    budgetView.textContent =
      `Budget: step ${budget.steps.current}/${budget.steps.limit} · ` +
      `${budget.tokens.used}/${budget.tokens.limit} token · ` +
      `${budget.time_seconds.remaining}s`;
    timeline.statusBlock.replaceChildren(goal, progress, remaining, budgetView);
    timeline.statusBlock.hidden = false;
  }

  function upsertTimelineItem(timeline, key, data) {
    let item = timeline.items.get(key);
    if (!item) {
      item = document.createElement("div");
      item.className = `timeline-item ${data.kind === "tool" ? "is-tool" : "is-model"}`;
      item.setAttribute("role", "listitem");

      const dot = document.createElement("span");
      dot.className = "timeline-dot";
      dot.setAttribute("aria-hidden", "true");
      const body = document.createElement("div");
      body.className = "timeline-item-body";
      const name = document.createElement("strong");
      name.className = "timeline-item-name";
      const description = document.createElement("small");
      description.className = "timeline-item-description";
      body.append(name, description);
      const meta = document.createElement("span");
      meta.className = "timeline-item-meta";
      item.append(dot, body, meta);
      timeline.list.append(item);
      timeline.items.set(key, item);
    }

    item.querySelector(".timeline-item-name").textContent = data.name;
    item.querySelector(".timeline-item-description").textContent = data.description;
    const metadata = [formatDuration(data.durationMs)];
    if (data.totalTokens) metadata.push(`${data.totalTokens} token`);
    if (data.cacheHitRate !== undefined && data.cacheHitRate !== null) {
      metadata.push(`cache ${(data.cacheHitRate * 100).toFixed(1)}%`);
    }
    item.querySelector(".timeline-item-meta").textContent = metadata.filter(Boolean).join(" · ");
    setTimelineItemState(item, data.state);
    timeline.count.textContent = `${timeline.items.size} hoạt động`;
  }

  function updateTimelineFromEvent(timeline, event) {
    updateAgentStatusBlock(timeline, event.agent_status);
    const isTool = event.status.startsWith("tool_");
    const key = isTool
      ? `tool-${event.step}-${event.tool?.call_id || event.tool?.name}`
      : `model-${event.step}`;
    const state =
      event.status === "model_failed" ||
      event.success === false ||
      (event.status === "tool_approval_resolved" && event.approval?.approved === false)
        ? "failed"
        : event.status.endsWith("completed")
          ? "completed"
          : "running";
    upsertTimelineItem(timeline, key, {
      kind: isTool ? "tool" : "model",
      name: isTool ? `Tool · ${event.tool?.name || "Không xác định"}` : `Step ${event.step} · Model`,
      description: event.message,
      state,
      durationMs: event.duration_ms,
      totalTokens: event.total_tokens,
      cacheHitRate: event.prompt_cache_hit_rate,
    });
    timeline.titleText.textContent = event.message;
    timeline.root.classList.toggle("has-failure", state === "failed");
    messageList.scrollTop = messageList.scrollHeight;
  }

  function finishTimeline(timeline, statusValue, totalTokens = 0, cacheHitRate = null) {
    stopTimelineClock(timeline);
    if (timeline.poll !== null) {
      window.clearTimeout(timeline.poll);
      timeline.poll = null;
    }
    timeline.terminal = true;
    if (statusValue !== "completed") {
      for (const item of timeline.items.values()) {
        if (item.classList.contains("is-running")) setTimelineItemState(item, "failed");
      }
      for (const [id, row] of timeline.progressItems) {
        if (row.state === "running") {
          updatePipelineProgress(timeline, {
            id, label: row.label.textContent, state: statusValue === "cancelled" ? "cancelled" : "failed",
            duration_ms: performance.now() - row.clockStarted,
          });
        }
      }
    }
    timeline.root.classList.remove("is-running");
    timeline.root.classList.add(statusValue === "completed" ? "is-completed" : "is-failed");
    timeline.titleText.textContent =
      statusValue === "completed" ? "Agent đã hoàn thành"
        : statusValue === "cancelled" ? "Agent đã dừng" : "Agent đã dừng do lỗi";
    const parts = [`${timeline.items.size} hoạt động`];
    if (totalTokens) parts.push(`${totalTokens} token`);
    if (cacheHitRate !== null) parts.push(`cache ${(cacheHitRate * 100).toFixed(1)}%`);
    timeline.count.textContent = parts.join(" · ");
    const remaining = timeline.statusBlock.querySelector(".status-remaining");
    if (remaining && statusValue === "completed") remaining.textContent = "Còn lại: Đã hoàn thành.";
  }

  function addResumeButton(timeline, runId) {
    if (!runId || timeline.list.querySelector(".resume-run-button")) return;
    const resumeButton = document.createElement("button");
    resumeButton.type = "button";
    resumeButton.className = "resume-run-button";
    resumeButton.textContent = "Tiếp tục từ checkpoint";
    resumeButton.addEventListener("click", async () => {
      if (sendButton.disabled) return;
      clearError();
      setBusy(true);
      resumeButton.disabled = true;
      try {
        await streamResume(runId);
        await refreshConversationList();
        resumeButton.remove();
      } catch (error) {
        showError(error.message || "Không thể resume agent run.");
        resumeButton.disabled = false;
      } finally {
        setBusy(false);
      }
    });
    timeline.list.append(resumeButton);
  }

  function requestToolApproval(timeline, event, runId) {
    return new Promise((resolve) => {
      const approval = event.approval;
      const card = document.createElement("div");
      card.className = "tool-approval-card";

      const heading = document.createElement("strong");
      heading.textContent = `Cho phép tool ${event.tool?.name || "không xác định"}?`;
      const explanation = document.createElement("p");
      explanation.textContent = "Kiểm tra tham số trước khi cho phép Agent tiếp tục.";
      const argumentsView = document.createElement("pre");
      argumentsView.textContent = JSON.stringify(approval.arguments || {}, null, 2);
      const feedback = document.createElement("small");
      feedback.className = "approval-feedback";

      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const denyButton = document.createElement("button");
      denyButton.type = "button";
      denyButton.className = "approval-deny";
      denyButton.textContent = "Từ chối";
      const approveButton = document.createElement("button");
      approveButton.type = "button";
      approveButton.className = "approval-approve";
      approveButton.textContent = "Cho phép";
      actions.append(denyButton, approveButton);
      card.append(heading, explanation, argumentsView, feedback, actions);
      timeline.list.append(card);
      messageList.scrollTop = messageList.scrollHeight;

      async function decide(approved) {
        denyButton.disabled = true;
        approveButton.disabled = true;
        feedback.textContent = "Đang gửi quyết định…";
        try {
          await requestJson(
            `/api/conversations/${activeConversationId}/runs/${runId}/approvals/${approval.id}`,
            { method: "POST", body: JSON.stringify({ approved }) },
          );
          card.classList.add(approved ? "is-approved" : "is-denied");
          feedback.textContent = approved ? "Đã cho phép." : "Đã từ chối.";
          resolve();
        } catch (error) {
          feedback.textContent = error.message || "Không thể gửi quyết định.";
          denyButton.disabled = false;
          approveButton.disabled = false;
        }
      }

      denyButton.addEventListener("click", () => decide(false));
      approveButton.addEventListener("click", () => decide(true));
    });
  }

  function canResumeRun(run) {
    return run?.status === "failed" && Number.isInteger(run.checkpoint_revision)
      && run.checkpoint_revision >= 0;
  }

  async function addAvailableResumeButton(timeline, runId) {
    if (!runId) return;
    try {
      const runs = await requestJson(`/api/conversations/${activeConversationId}/runs`);
      if (canResumeRun(runs.find((run) => run.id === runId))) {
        addResumeButton(timeline, runId);
      }
    } catch {
      // A failed availability check must not offer an unverified resume action.
    }
  }

  function renderStoredTimeline(assistantBubble, run) {
    const timeline = createAgentTimeline(assistantBubble, false);
    updateStoredTimeline(timeline, run);
    if (!timeline.terminal) pollStoredTimeline(timeline, activeConversationId, run.id);
  }

  function updateStoredTimeline(timeline, run) {
    updateTimelineSearchMode(timeline, run.rag_search_mode);
    const startedAt = Date.parse(run.started_at || run.created_at);
    const finishedAt = run.finished_at ? Date.parse(run.finished_at) : Date.now();
    const elapsed = Number.isFinite(startedAt) ? Math.max(0, finishedAt - startedAt) : 0;
    timeline.timer.textContent = formatElapsed(elapsed);
    for (const data of run.progress || []) {
      const runningElapsed = data.state === "running" && data.started_at
        ? Date.now() - Date.parse(data.started_at) : data.duration_ms;
      updatePipelineProgress(timeline, { ...data, duration_ms: Math.max(0, runningElapsed || 0) });
    }
    for (const step of run.steps) {
      upsertTimelineItem(timeline, `model-${step.step_number}-${step.attempt_number}`, {
        kind: "model",
        name:
          `Step ${step.step_number} · ${step.model_name || "Model"}` +
          (step.attempt_number > 1 ? ` · lần thử ${step.attempt_number}` : ""),
        description: step.prompt_version === "document-qa-plan-v1"
          ? (step.status === "completed" ? "Đã phân tích câu hỏi và ngữ cảnh." : "Không hoàn tất phân tích câu hỏi.")
          : step.summary || "Model đã xử lý bước này.",
        state: step.status === "completed" ? "completed" : "failed",
        durationMs: step.duration_ms,
        totalTokens: step.total_tokens,
        cacheHitRate: step.prompt_cache_hit_rate,
      });
      for (const tool of step.tool_calls) {
        upsertTimelineItem(timeline, `stored-tool-${tool.id}`, {
          kind: "tool",
          name: `Tool · ${tool.tool_name}`,
          description:
            tool.status === "completed"
              ? `Tool ${tool.tool_name} đã chạy xong.`
              : `Tool ${tool.tool_name} ${tool.status}.`,
          state: tool.status === "completed" ? "completed" : "failed",
          durationMs: tool.duration_ms,
        });
      }
    }
    if (["running", "queued", "waiting_approval"].includes(run.status)) {
      startTimelineClock(timeline, elapsed);
      timeline.titleText.textContent = run.status === "waiting_approval"
        ? "Đang chờ bạn phê duyệt" : "Agent đang xử lý";
    } else {
      finishTimeline(timeline, run.status, run.total_tokens, run.prompt_cache_hit_rate);
    }
    if (run.available_tool_count !== null && run.loaded_tool_count !== null) {
      timeline.count.textContent += ` · tools ${run.loaded_tool_count}/${run.available_tool_count}`;
    }
    if (canResumeRun(run)) {
      addResumeButton(timeline, run.id);
    }
  }

  function pollStoredTimeline(timeline, conversationId, runId) {
    if (timeline.terminal || !timeline.root.isConnected || conversationId !== activeConversationId) return;
    timeline.poll = window.setTimeout(async () => {
      timeline.poll = null;
      if (!timeline.root.isConnected || conversationId !== activeConversationId) return;
      try {
        const runs = await requestJson(`/api/conversations/${conversationId}/runs`);
        const run = runs.find((item) => item.id === runId);
        if (!run) {
          stopTimelineClock(timeline);
          timeline.titleText.textContent = "Không tìm thấy lần chạy";
          return;
        }
        updateStoredTimeline(timeline, run);
      } catch {
        stopTimelineClock(timeline);
        timeline.titleText.textContent = "Mất kết nối cập nhật tiến trình";
      }
      pollStoredTimeline(timeline, conversationId, runId);
    }, 3000);
  }

  async function loadConversations(preferredId = null) {
    const conversations = await requestJson("/api/conversations");
    if (conversations.length === 0) {
      const created = await createConversation();
      return loadConversations(created.id);
    }
    const selected =
      conversations.find((item) => item.id === preferredId) ||
      conversations.find((item) => item.id === activeConversationId) ||
      conversations[0];
    activeConversationId = selected.id;
    renderConversationItems(conversations);
    conversationList.querySelector(".conversation-row.is-active")?.scrollIntoView({ block: "nearest" });
    await selectConversation(selected, false);
  }

  async function createConversation() {
    return requestJson("/api/conversations", { method: "POST", body: "{}" });
  }

  async function selectConversation(conversation, refreshList = true) {
    if (streamInProgress || documentMutationPending) return;
    activeConversationId = conversation.id;
    title.textContent = conversation.title;
    document.querySelectorAll(".conversation-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.id === conversation.id);
      item.setAttribute("aria-current", item.dataset.id === conversation.id ? "true" : "false");
      item.parentElement.classList.toggle("is-active", item.dataset.id === conversation.id);
    });
    const [messages, runs] = await Promise.all([
      requestJson(`/api/conversations/${conversation.id}/messages`),
      requestJson(`/api/conversations/${conversation.id}/runs`),
      documentPanel?.setConversation(conversation.id),
    ]);
    if (activeConversationId !== conversation.id) return;
    const runsByAssistantMessage = new Map(
      runs.filter((run) => run.assistant_message_id).map((run) => [run.assistant_message_id, run]),
    );
    const failedRunsByUserMessage = new Map();
    for (const run of runs) {
      if (run.status !== "failed" || run.assistant_message_id || !run.user_message_id) continue;
      const items = failedRunsByUserMessage.get(run.user_message_id) || [];
      items.push(run);
      failedRunsByUserMessage.set(run.user_message_id, items);
    }
    messageList.replaceChildren();
    if (messages.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-chat";
      empty.dataset.emptyChat = "";
      empty.innerHTML =
        '<div class="agent-mark">PA</div><h2>Bạn muốn bắt đầu với điều gì?</h2>' +
        "<p>Hãy gửi một câu hỏi. Lịch sử hội thoại sẽ được lưu riêng trong tài khoản của bạn.</p>";
      messageList.append(empty);
    } else {
      messages.forEach((message) => {
        const bubble = addMessage(message.role, message.content);
        if (message.role === "assistant") renderCitations(bubble, message.citations, message.id, conversation.id);
        const run = runsByAssistantMessage.get(message.id);
        if (message.role === "assistant" && run) renderStoredTimeline(bubble, run);
        if (message.role === "user") {
          for (const failedRun of failedRunsByUserMessage.get(message.id) || []) {
            const failedBubble = addMessage("assistant", "Lần chạy trước đã dừng do lỗi.");
            failedBubble.classList.add("is-error");
            renderStoredTimeline(failedBubble, failedRun);
          }
        }
      });
    }
    if (refreshList) await refreshConversationList();
  }

  async function refreshConversationList() {
    const conversations = await requestJson("/api/conversations");
    const active = conversations.find((item) => item.id === activeConversationId);
    if (active) title.textContent = active.title;
    renderConversationItems(conversations);
  }

  function parseSseBlock(block) {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return null;
    return { event, data: JSON.parse(dataLines.join("\n")) };
  }

  async function consumeAgentStream(response, { assistantBubble, timeline }) {
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Không thể bắt đầu agent run.");
    }
    if (!response.body) throw new Error("Trình duyệt không hỗ trợ streaming.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let runId = null;
    let assistantMarkdown = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const parsed = parseSseBlock(block);
          if (!parsed) continue;
          if (parsed.event === "start") {
            runId = parsed.data.run_id;
            timeline.root.dataset.runId = runId;
            updateTimelineSearchMode(timeline, parsed.data.rag_search_mode);
          }
          if (parsed.event === "progress") {
            updatePipelineProgress(timeline, parsed.data);
            messageList.scrollTop = messageList.scrollHeight;
          }
          if (parsed.event === "delta") {
            assistantMarkdown += parsed.data.content;
            window.renderMarkdown(assistantBubble, assistantMarkdown);
            messageList.scrollTop = messageList.scrollHeight;
          }
          if (parsed.event === "agent_status") {
            status.textContent = parsed.data.message;
            updateTimelineFromEvent(timeline, parsed.data);
            if (parsed.data.status === "tool_approval_required") {
              await requestToolApproval(timeline, parsed.data, runId);
            }
          }
          if (parsed.event === "error") {
            finishTimeline(timeline, "failed");
            if (parsed.data.resumable !== false) await addAvailableResumeButton(timeline, runId);
            if (!assistantMarkdown) {
              assistantMarkdown = parsed.data.message;
              window.renderMarkdown(assistantBubble, assistantMarkdown);
            }
            assistantBubble.classList.add("is-error");
            throw new Error(parsed.data.message);
          }
          if (parsed.event === "done") {
            renderCitations(assistantBubble, parsed.data.citations, parsed.data.message_id, activeConversationId);
            const usage = parsed.data.usage || {};
            finishTimeline(
              timeline,
              "completed",
              usage.total_tokens || 0,
              usage.prompt_cache_hit_rate,
            );
            const cacheText =
              usage.prompt_cache_hit_rate === null
                ? ""
                : ` · cache ${(usage.prompt_cache_hit_rate * 100).toFixed(1)}%`;
            status.textContent = `Đã trả lời · ${usage.total_tokens || 0} token${cacheText}`;
          }
        }
        if (done) break;
      }
      if (!timeline.terminal) throw new Error("Kết nối đã đóng trước khi lần chạy hoàn tất.");
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
      stopTimelineClock(timeline);
    }
  }

  async function requestAgentStream(url, options) {
    const assistantBubble = addMessage("assistant");
    const timeline = createAgentTimeline(assistantBubble);
    try {
      const response = await fetch(url, options);
      await consumeAgentStream(response, { assistantBubble, timeline });
    } catch (error) {
      if (!timeline.terminal) {
        finishTimeline(timeline, "failed");
        await addAvailableResumeButton(timeline, timeline.root.dataset.runId);
      }
      if (!assistantBubble.textContent) window.renderMarkdown(assistantBubble, error.message);
      assistantBubble.classList.add("is-error");
      throw error;
    } finally {
      stopTimelineClock(timeline);
    }
  }

  async function streamReply(content) {
    await requestAgentStream(`/api/conversations/${activeConversationId}/messages`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, rag_search_mode: ragSearchMode?.value || "fast" }),
    });
  }

  async function streamResume(runId) {
    await requestAgentStream(
      `/api/conversations/${activeConversationId}/runs/${runId}/resume`,
      { method: "POST", credentials: "same-origin" },
    );
  }

  chatApp.querySelector("[data-new-chat]").addEventListener("click", async () => {
    if (sendButton.disabled || conversationMutationPending) return;
    clearError();
    conversationMutationPending = true;
    updateConversationControls();
    try {
      const conversation = await createConversation();
      await loadConversations(conversation.id);
    } catch (error) {
      showError(error.message);
    } finally {
      conversationMutationPending = false;
      updateConversationControls();
      textarea.focus();
    }
  });

  messageForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (sendButton.disabled) return;
    const content = textarea.value.trim();
    if (!content || !activeConversationId) return;
    clearError();
    textarea.value = "";
    addMessage("user", content);
    setBusy(true);
    try {
      await streamReply(content);
      await refreshConversationList();
    } catch (error) {
      showError(error.message || "Không thể nhận phản hồi.");
    } finally {
      setBusy(false);
      textarea.focus();
    }
  });

  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  });

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      messageForm.requestSubmit();
    }
  });

  documentPanel = window.DocumentLibrary.createChatPanel(chatApp, {
    onBusyChange(isBusy) {
      documentMutationPending = isBusy;
      updateConversationControls();
    },
  });
  loadConversations().catch((error) => showError(error.message));
}
