(function () {
  "use strict";

  const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

  function appendText(parent, value) {
    if (value) parent.append(document.createTextNode(value));
  }

  function safeLink(value) {
    const href = value.trim();
    if (href.startsWith("/") || href.startsWith("#")) return href;
    try {
      const url = new URL(href, window.location.origin);
      return SAFE_LINK_PROTOCOLS.has(url.protocol) ? href : null;
    } catch {
      return null;
    }
  }

  function appendInline(parent, source) {
    let cursor = 0;
    let plainText = "";

    function flushText() {
      appendText(parent, plainText);
      plainText = "";
    }

    function appendWrapped(tagName, content) {
      flushText();
      const element = document.createElement(tagName);
      appendInline(element, content);
      parent.append(element);
    }

    while (cursor < source.length) {
      if (source[cursor] === "\\" && cursor + 1 < source.length) {
        plainText += source[cursor + 1];
        cursor += 2;
        continue;
      }

      if (source[cursor] === "`" && !source.startsWith("```", cursor)) {
        const end = source.indexOf("`", cursor + 1);
        if (end !== -1) {
          flushText();
          const code = document.createElement("code");
          code.textContent = source.slice(cursor + 1, end);
          parent.append(code);
          cursor = end + 1;
          continue;
        }
      }

      const strongMarker = source.startsWith("**", cursor)
        ? "**"
        : source.startsWith("__", cursor)
          ? "__"
          : null;
      if (strongMarker) {
        const end = source.indexOf(strongMarker, cursor + 2);
        if (end !== -1) {
          appendWrapped("strong", source.slice(cursor + 2, end));
          cursor = end + 2;
          continue;
        }
      }

      if (source.startsWith("~~", cursor)) {
        const end = source.indexOf("~~", cursor + 2);
        if (end !== -1) {
          appendWrapped("del", source.slice(cursor + 2, end));
          cursor = end + 2;
          continue;
        }
      }

      if (source[cursor] === "[") {
        const match = source.slice(cursor).match(/^\[([^\]]+)\]\((\S+?)(?:\s+["'].*?["'])?\)/);
        if (match) {
          const href = safeLink(match[2]);
          flushText();
          if (href) {
            const link = document.createElement("a");
            link.href = href;
            appendInline(link, match[1]);
            if (!href.startsWith("/") && !href.startsWith("#")) {
              link.target = "_blank";
              link.rel = "noopener noreferrer";
            }
            parent.append(link);
          } else {
            appendText(parent, match[1]);
          }
          cursor += match[0].length;
          continue;
        }
      }

      if (source[cursor] === "<") {
        const match = source.slice(cursor).match(/^<(https?:\/\/[^ >]+|mailto:[^ >]+)>/i);
        if (match) {
          flushText();
          const link = document.createElement("a");
          link.href = match[1];
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = match[1];
          parent.append(link);
          cursor += match[0].length;
          continue;
        }
      }

      if (source[cursor] === "*" || source[cursor] === "_") {
        const marker = source[cursor];
        const end = source.indexOf(marker, cursor + 1);
        if (end > cursor + 1) {
          appendWrapped("em", source.slice(cursor + 1, end));
          cursor = end + 1;
          continue;
        }
      }

      if (source[cursor] === "\n") {
        const hardBreak = plainText.endsWith("  ");
        if (hardBreak) plainText = plainText.slice(0, -2);
        flushText();
        if (hardBreak) parent.append(document.createElement("br"));
        else appendText(parent, " ");
        cursor += 1;
        continue;
      }

      plainText += source[cursor];
      cursor += 1;
    }
    flushText();
  }

  function splitTableRow(line) {
    let value = line.trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split(/(?<!\\)\|/).map((cell) => cell.trim().replaceAll("\\|", "|"));
  }

  function isTableDivider(line) {
    const cells = splitTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function startsBlock(lines, index) {
    const line = lines[index] || "";
    return (
      /^\s*$/.test(line) ||
      /^\s*```/.test(line) ||
      /^ {0,3}#{1,6}\s+/.test(line) ||
      /^ {0,3}>\s?/.test(line) ||
      /^\s*([-+*]|\d+[.)])\s+/.test(line) ||
      /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line) ||
      (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1]))
    );
  }

  function renderMarkdown(container, markdown) {
    const fragment = document.createDocumentFragment();
    const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
    let index = 0;

    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      const fence = line.match(/^\s*```([\w+-]*)\s*$/);
      if (fence) {
        const codeLines = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        if (fence[1]) {
          code.className = `language-${fence[1].toLowerCase()}`;
          pre.dataset.language = fence[1].toLowerCase();
        }
        code.textContent = codeLines.join("\n");
        pre.append(code);
        fragment.append(pre);
        continue;
      }

      const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        const element = document.createElement(`h${heading[1].length}`);
        appendInline(element, heading[2]);
        fragment.append(element);
        index += 1;
        continue;
      }

      if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        fragment.append(document.createElement("hr"));
        index += 1;
        continue;
      }

      if (/^ {0,3}>\s?/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && /^ {0,3}>\s?/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^ {0,3}>\s?/, ""));
          index += 1;
        }
        const quote = document.createElement("blockquote");
        renderMarkdown(quote, quoteLines.join("\n"));
        fragment.append(quote);
        continue;
      }

      const listMatch = line.match(/^\s*([-+*]|\d+[.)])\s+(.+)$/);
      if (listMatch) {
        const ordered = /^\d/.test(listMatch[1]);
        const list = document.createElement(ordered ? "ol" : "ul");
        if (ordered) list.start = Number.parseInt(listMatch[1], 10);
        while (index < lines.length) {
          const itemMatch = lines[index].match(/^\s*([-+*]|\d+[.)])\s+(.+)$/);
          if (!itemMatch || /^\d/.test(itemMatch[1]) !== ordered) break;
          const item = document.createElement("li");
          const task = itemMatch[2].match(/^\[([ xX])\]\s+(.+)$/);
          if (task) {
            item.className = "task-list-item";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = task[1].toLowerCase() === "x";
            checkbox.disabled = true;
            item.append(checkbox);
            appendInline(item, task[2]);
          } else {
            appendInline(item, itemMatch[2]);
          }
          list.append(item);
          index += 1;
        }
        fragment.append(list);
        continue;
      }

      if (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1])) {
        const headers = splitTableRow(line);
        const dividers = splitTableRow(lines[index + 1]);
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        headers.forEach((content, cellIndex) => {
          const cell = document.createElement("th");
          const divider = dividers[cellIndex] || "";
          if (divider.startsWith(":") && divider.endsWith(":")) cell.style.textAlign = "center";
          else if (divider.endsWith(":")) cell.style.textAlign = "right";
          appendInline(cell, content);
          headerRow.append(cell);
        });
        thead.append(headerRow);
        table.append(thead);
        index += 2;
        const tbody = document.createElement("tbody");
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          const row = document.createElement("tr");
          splitTableRow(lines[index]).forEach((content, cellIndex) => {
            const cell = document.createElement("td");
            const divider = dividers[cellIndex] || "";
            if (divider.startsWith(":") && divider.endsWith(":")) cell.style.textAlign = "center";
            else if (divider.endsWith(":")) cell.style.textAlign = "right";
            appendInline(cell, content);
            row.append(cell);
          });
          tbody.append(row);
          index += 1;
        }
        table.append(tbody);
        const wrapper = document.createElement("div");
        wrapper.className = "markdown-table-wrap";
        wrapper.append(table);
        fragment.append(wrapper);
        continue;
      }

      const paragraphLines = [line];
      index += 1;
      while (index < lines.length && !startsBlock(lines, index)) {
        paragraphLines.push(lines[index]);
        index += 1;
      }
      const paragraph = document.createElement("p");
      appendInline(paragraph, paragraphLines.join("\n"));
      fragment.append(paragraph);
    }

    container.replaceChildren(fragment);
  }

  window.renderMarkdown = renderMarkdown;
})();
