// app.js — opencode-style chat renderer for SimpleAgent's QWebEngineView.
// No framework. Receives JSON events pushed from Python via window.__appendEvent.

(function () {
  "use strict";

  const root = document.getElementById("transcript");
  const items = [];            // {el, kind, ...} — mirrors Python's self.items
  let pendingAssistant = null; // the currently-streaming message element
  let pendingTool = null;      // the most recent tool_start card (awaiting its result)
  let pendingReasoning = null; // the currently-streaming reasoning block

  // ---- helpers ----
  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function escapeText(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function resultSummary(result) {
    const t = (result || "").trim();
    if (!t) return "done";
    for (const p of ["SUCCESS", "ERROR", "BLOCKED", "CANCELLED"]) {
      if (t.toUpperCase().startsWith(p)) {
        const rest = t.slice(p.length).replace(/^[\s:]+/, "").split(/\s+/).slice(0, 6).join(" ");
        return p.toLowerCase() + (rest ? " - " + rest : "");
      }
    }
    const lines = t.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length > 1) return lines.length + " lines";
    return t.split(/\s+/).slice(0, 8).join(" ");
  }

  // ---- markdown pipeline ----
  let markedLib = null;
  let morphdomLib = null;
  try { markedLib = window.marked; } catch {}
  try { morphdomLib = window.morphdom; } catch {}

  if (markedLib) {
    markedLib.setOptions({ breaks: false, gfm: true });
  }

  // Shiki worker + request tracking
  let shikiWorker = null;
  let shikiReady = false;
  let shikiFailed = false;
  const shikiPending = new Map(); // id -> {pre, code, lang}
  let shikiSeq = 0;

  function initShikiWorker() {
    if (shikiWorker) return;
    try {
      shikiWorker = new Worker("shiki.worker.js", { type: "module" });
      shikiWorker.onmessage = (e) => {
        const d = e.data;
        if (d.type === "ready") { shikiReady = true; flushPendingShiki(); return; }
        if (d.type === "error" && !d.id) { shikiFailed = true; flushPendingShiki(); return; }
        const req = shikiPending.get(d.id);
        if (!req) return;
        shikiPending.delete(d.id);
        if (d.type === "ok" && req.pre && document.contains(req.pre)) {
          // Shiki returns a full <pre class="shiki">…</pre>; replace in place.
          // outerHTML swaps the live node (the wrapper + copy button remain).
          req.pre.outerHTML = d.html;
        }
      };
    } catch {
      shikiFailed = true;
    }
  }

  function flushPendingShiki() {
    if (shikiReady) {
      for (const [id, req] of shikiPending) {
        const theme = currentShikiTheme();
        shikiWorker.postMessage({ id, code: req.code, lang: req.lang, theme });
      }
    } else {
      // Worker not available: leave plain <pre><code> as the graceful fallback.
      shikiPending.clear();
    }
  }

  function requestHighlight(pre, code, lang) {
    if (shikiFailed) return; // fallback: plain pre/code is already rendered
    initShikiWorker();
    const id = ++shikiSeq;
    shikiPending.set(id, { pre, code, lang });
    if (shikiReady) {
      const theme = currentShikiTheme();
      shikiWorker.postMessage({ id, code, lang, theme });
    }
  }

  function currentShikiTheme() {
    return getComputedStyle(document.body).getPropertyValue("--shiki-theme").trim() || "github-dark";
  }

  // Re-highlight all code blocks on theme change (called from renderTheme).
  function rehighlightForTheme() {
    if (!shikiReady) return;
    // Re-highlight every code block using the lang stashed on its wrapper.
    root.querySelectorAll(".markdown-code").forEach((wrapper) => {
      const pre = wrapper.querySelector("pre");
      if (!pre) return;
      const lang = wrapper.dataset.lang || "text";
      requestHighlight(pre, pre.textContent, lang);
    });
  }

  function mdToHtml(text) {
    if (!markedLib) {
      // Fallback: escape + <br>.
      const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return esc(text || "").replace(/\n/g, "<br>");
    }
    return markedLib.parse(text || "");
  }

  // Render markdown into a container, decorate code blocks (Shiki + copy), and
  // morphdom-patch the result into the live DOM node.
  function renderMarkdownInto(container, text, allowHighlight) {
    const html = mdToHtml(text);
    const next = el("div", "", html);

    // Decorate: wrap each <pre> in .markdown-code with a copy button (on the
    // detached `next` tree). We do NOT request highlighting here — morphdom
    // moves these nodes into the live DOM, after which we highlight live nodes.
    next.querySelectorAll("pre").forEach((pre) => {
      const code = pre.querySelector("code");
      const lang = code ? (code.className.match(/language-([\w-]+)/) || [])[1] : null;
      const wrapper = el("div", "markdown-code");
      wrapper.dataset.lang = lang || "text";
      pre.parentElement.replaceChild(wrapper, pre);
      wrapper.appendChild(pre);
      const btn = el("button", "copy-btn", "Copy");
      btn.dataset.slot = "markdown-copy-button";
      btn.addEventListener("click", () => {
        navigator.clipboard.writeText(code ? code.textContent : "").then(() => {
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 2000);
        });
      });
      wrapper.appendChild(btn);
    });

    if (morphdomLib) {
      morphdomLib(container, next, {
        onBeforeElUpdated: (fromEl, toEl) => {
          // Preserve copy buttons across patches.
          if (fromEl.dataset && fromEl.dataset.slot === "markdown-copy-button") return false;
          return true;
        },
      });
    } else {
      container.innerHTML = next.innerHTML;
    }

    // Highlight live code blocks AFTER morphdom attached them. Run only when
    // allowed (skip during streaming for performance; highlight on the final render).
    if (allowHighlight) highlightCodeBlocks(container);
  }

  // Highlight any live code blocks that Shiki hasn't processed yet. Skips
  // blocks already carrying a shiki class (idempotent / theme-safe re-renders).
  function highlightCodeBlocks(container) {
    container.querySelectorAll(".markdown-code").forEach((wrapper) => {
      const pre = wrapper.querySelector("pre");
      if (!pre) return;
      if (pre.className.indexOf("shiki") !== -1) return; // already highlighted
      const lang = wrapper.dataset.lang || "text";
      const code = pre.querySelector("code");
      const text = code ? code.textContent : pre.textContent;
      requestHighlight(pre, text, lang);
    });
  }

  // ---- paced reveal (ported from opencode's PacedMarkdown) ----
  const PACE_MS = 24;
  const IMMEDIATE = 512;
  const SNAP = /[\s.,!?;:)\]]/;

  function step(size) {
    if (size <= 12) return 2;
    if (size <= 48) return 4;
    if (size <= 96) return 8;
    return Math.min(256, Math.ceil(size / 4));
  }
  function nextSlice(text, start) {
    const end = Math.min(text.length, start + step(text.length - start));
    const max = Math.min(text.length, end + 8);
    for (let i = end; i < max; i++) if (SNAP.test(text[i] || "")) return i + 1;
    return end;
  }

  // Per-message pacing state, keyed by the .md container element.
  const revealState = new WeakMap(); // el -> {shown, timer}

  function pacedRender(container, fullText, isStreaming) {
    let st = revealState.get(container);
    if (!st) { st = { shown: "", timer: null }; revealState.set(container, st); }

    const clear = () => { if (st.timer) { clearTimeout(st.timer); st.timer = null; } };
    // Highlight only on the final (non-streaming) render, not every paced tick.
    const sync = (t) => { st.shown = t; renderMarkdownInto(container, t, !isStreaming); };

    clear();
    if (!isStreaming) { sync(fullText); return; }

    // Streaming: if the new text doesn't extend what's shown, snap to it.
    if (!fullText.startsWith(st.shown) || fullText.length < st.shown.length) {
      sync(fullText); return;
    }
    const delta = fullText.length - st.shown.length;
    if (delta <= IMMEDIATE) { sync(fullText); return; }
    if (st.shown.length === fullText.length) return;

    const run = () => {
      st.timer = null;
      const end = nextSlice(fullText, st.shown.length);
      sync(fullText.slice(0, end));
      if (end < fullText.length) st.timer = setTimeout(run, PACE_MS);
    };
    st.timer = setTimeout(run, PACE_MS);
  }

  // ---- tool cards ----
  const TOOL_ICONS = {
    editor: "M11 4H4v14h14v-7M18.5 2.5 22 6 9 19l-4 1 1-4 12.5-13.5z",
    run_command: "M4 5h16v10H4zM6 19h4M14 19h4M8 17v2M16 17v2",
    read_files: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zM12 9a3 3 0 100 6 3 3 0 000-6z",
    search_codebase: "M21 21l-6-6M10 4a6 6 0 100 12 6 6 0 000-12z",
    fetch_web: "M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2c2.5 2.5 4 6 4 10s-1.5 7.5-4 10c-2.5-2.5-4-6-4-10s1.5-7.5 4-10z",
    memory: "M9 2h6v4l4 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V8l4-2V2z",
    skills: "M5 3h14v18l-7-4-7 4V3z",
    sessions: "M12 7v5l3 2M12 2a10 10 0 100 20 10 10 0 000-20z",
    ask_question: "M21 11.5a8.5 8.5 0 11-17 0 8.5 8.5 0 0117 0zM9 10h6M9 13h3",
  };

  function iconSvg(name) {
    const d = TOOL_ICONS[name] || TOOL_ICONS.ask_question;
    return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="' + d + '"/></svg>';
  }

  function shortDetail(argsText) {
    try {
      const a = JSON.parse(argsText || "{}");
      for (const k of ["path", "paths", "source", "command", "pattern", "query", "name"]) {
        if (a[k] != null) return String(a[k]);
      }
    } catch {}
    return "";
  }

  function statusIcon(state) {
    if (state === "pending")
      return '<svg class="status pending" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 000 20 10 10 0 000-20zM12 6v6l4 2"/></svg>';
    return '<svg class="status" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
  }

  function buildToolCard(ev) {
    const card = el("div", "tool-card");
    const detail = shortDetail(ev.args);
    const head = el("div", "head");
    head.innerHTML = iconSvg(ev.name) +
      '<span class="title"></span>' +
      (detail ? '<span class="subtitle"></span>' : '') +
      '<span class="summary">running</span>' +
      statusIcon("pending");
    head.querySelector(".title").textContent = ev.name + "()";
    if (detail) head.querySelector(".subtitle").textContent = detail;
    card.appendChild(head);
    card._expanded = false;
    card._result = el("div", "result");
    card._result.style.display = "none";
    card.appendChild(card._result);
    head.addEventListener("click", () => {
      card._expanded = !card._expanded;
      card._result.style.display = card._expanded ? "block" : "none";
    });
    return card;
  }

  // ---- reasoning ----
  function heading(text) {
    const m = (text || "").match(/^\s{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$/m);
    if (m && m[1]) return m[1].replace(/[*_`~]/g, "").trim();
    const s = (text || "").match(/^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*$/m);
    if (s && s[1]) return s[1].trim();
    return "";
  }

  // ---- renderers ----
  function renderUser(text) {
    const node = el("div", "item item-user");
    const bubble = el("div", "bubble");
    bubble.textContent = text;
    node.appendChild(bubble);
    return node;
  }

  function renderError(text) {
    return el("div", "item item-error", "<b>Error:</b> " + escapeText(text));
  }

  function renderDone() {
    if (pendingAssistant) {
      pendingAssistant.classList.remove("streaming");
      pendingAssistant = null;
    }
    // Stop any lingering reasoning shimmer if no final assistant message dropped it.
    if (pendingReasoning) {
      pendingReasoning._streaming = false;
      const toggle = pendingReasoning.querySelector(".toggle");
      if (toggle) {
        toggle.innerHTML = (pendingReasoning._expanded ? "▼" : "▶") + ' <b>Thinking</b>';
      }
    }
    return null;
  }

  function renderTheme(mode) {
    document.body.dataset.theme = mode === "light" ? "light" : "dark";
    rehighlightForTheme();
    return null;
  }

  // ---- handlers ----
  function handleAssistantMessage(ev) {
    // Drop any reasoning blocks shown while thinking, so the final transcript
    // is clean — only the answer remains (matches the existing add_agent behavior).
    if (pendingReasoning) {
      pendingReasoning.remove();
      pendingReasoning = null;
      for (let i = items.length - 1; i >= 0; i--) {
        if (items[i].kind === "reasoning") { items.splice(i, 1); break; }
      }
    }
    if (pendingAssistant == null) {
      pendingAssistant = el("div", "item item-agent streaming");
      pendingAssistant.innerHTML = '<div class="label">Agent</div><div class="md"></div>';
      append({ kind: "agent", el: pendingAssistant });
    }
    const md = pendingAssistant.querySelector(".md");
    if (md) pacedRender(md, ev.content, !!ev.streaming);
    if (!ev.streaming) {
      pendingAssistant.classList.remove("streaming");
      pendingAssistant = null;
    }
  }

  function handleReasoning(ev) {
    const streaming = !!ev.streaming;
    if (pendingReasoning == null) {
      pendingReasoning = el("div", "item item-reasoning");
      pendingReasoning.innerHTML =
        '<div class="toggle"></div>' +
        '<div class="body" style="display:none"></div>';
      pendingReasoning._expanded = false;
      pendingReasoning.querySelector(".toggle").addEventListener("click", () => {
        pendingReasoning._expanded = !pendingReasoning._expanded;
        pendingReasoning.querySelector(".body").style.display =
          pendingReasoning._expanded ? "block" : "none";
        updateToggleArrow();
      });
      append({ kind: "reasoning", el: pendingReasoning });
    }

    // Track current streaming state on the element so updateToggleArrow (bound
    // once on creation) reads fresh state instead of a stale first-call closure.
    pendingReasoning._streaming = streaming;

    const toggle = pendingReasoning.querySelector(".toggle");
    const body = pendingReasoning.querySelector(".body");

    if (streaming) {
      toggle.innerHTML = '<span class="shimmer"></span> Thinking';
      const h = heading(ev.content);
      if (h) toggle.innerHTML += ' <span style="opacity:.7">' + escapeText(h) + '</span>';
      if (pendingReasoning._expanded) renderMarkdownInto(body, ev.content, false);
    } else {
      toggle.innerHTML = (pendingReasoning._expanded ? "▼" : "▶") + ' <b>Thinking</b>';
      if (pendingReasoning._expanded) renderMarkdownInto(body, ev.content, true);
    }

    function updateToggleArrow() {
      if (!pendingReasoning || pendingReasoning._streaming) return;
      toggle.innerHTML = (pendingReasoning._expanded ? "▼" : "▶") + ' <b>Thinking</b>';
    }
  }

  function handleToolStart(ev) {
    const card = buildToolCard(ev);
    pendingTool = { name: ev.name, card };
    append({ kind: "tool", name: ev.name, el: card });
  }

  function handleToolResult(ev) {
    // Match the most recent pending tool of the same name (order-based).
    let target = pendingTool && pendingTool.name === ev.name ? pendingTool : null;
    if (!target) {
      for (let i = items.length - 1; i >= 0; i--) {
        if (items[i].kind === "tool" && items[i].name === ev.name) { target = { card: items[i].el }; break; }
      }
    }
    pendingTool = null;
    if (!target) { target = { card: buildToolCard(ev) }; append({ kind: "tool", name: ev.name, el: target.card }); }

    const card = target.card;
    const summary = card.querySelector(".summary");
    if (summary) summary.textContent = resultSummary(ev.result);
    const status = card.querySelector(".status");
    if (status) status.outerHTML = statusIcon("completed");
    if (card._result) {
      card._result.textContent = (ev.result || "").slice(0, 4000);
    }
  }

  function handleDiff(ev) {
    const card = el("div", "item diff-card");
    const head = el("div", "head");
    head.innerHTML =
      '<span class="path"></span>' +
      '<span class="added">+' + (ev.added || 0) + '</span>' +
      '<span class="removed">-' + (ev.removed || 0) + '</span>' +
      '<span class="chevron">▼</span>';
    head.querySelector(".path").textContent = ev.path;
    card.appendChild(head);

    const body = el("div", "diff-body");
    body.style.display = "none";
    (ev.diff || "").split(/\r?\n/).forEach((line) => {
      const row = el("div", "line");
      if (line.startsWith("+++") || line.startsWith("---")) row.classList.add("meta");
      else if (line.startsWith("@@")) row.classList.add("hunk");
      else if (line.startsWith("+")) row.classList.add("add");
      else if (line.startsWith("-")) row.classList.add("del");
      row.textContent = line || "\u00a0";
      body.appendChild(row);
    });
    card.appendChild(body);

    head.addEventListener("click", () => {
      const open = body.style.display === "block";
      body.style.display = open ? "none" : "block";
      head.querySelector(".chevron").textContent = open ? "▼" : "▶";
    });

    append({ kind: "diff", el: card });
  }

  // ---- dispatcher ----
  // Public hook called from Python: window.__appendEvent(jsonString)
  window.__appendEvent = function (jsonString) {
    let ev;
    try { ev = JSON.parse(jsonString); } catch { return; }
    const t = ev.type;

    if (t === "user") { append({ kind: "user", el: renderUser(ev.text) }); return; }
    if (t === "error") { append({ kind: "error", el: renderError(ev.text) }); return; }
    if (t === "done") { renderDone(); return; }
    if (t === "theme") { renderTheme(ev.mode); return; }
    if (t === "assistant_message") { handleAssistantMessage(ev); return; }
    if (t === "reasoning") { handleReasoning(ev); return; }
    if (t === "tool_start") { handleToolStart(ev); return; }
    if (t === "tool_result") { handleToolResult(ev); return; }
    if (t === "diff") { handleDiff(ev); return; }
  };

  function append(item) {
    items.push(item);
    root.appendChild(item.el);
    window.scrollTo(0, root.scrollHeight);
  }
})();