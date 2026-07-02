// app.js — opencode-style chat renderer for SimpleAgent's QWebEngineView.
// No framework. Receives JSON events pushed from Python via window.__appendEvent.

(function () {
  "use strict";

  const root = document.getElementById("transcript");
  const items = [];            // {el, kind, ...} — mirrors Python's self.items
  let pendingAssistant = null; // the currently-streaming message element
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

  // Both navigator.clipboard (needs a "secure context", which file: pages
  // don't get) and document.execCommand('copy') are unreliable in QtWebEngine
  // from a file:// origin. Instead, navigate to a custom 'copy:' pseudo-URL
  // that qt_app.py's _BridgePage intercepts and copies via Qt's own system
  // clipboard — that always works since it never touches the browser APIs.
  function copyToClipboard(text) {
    window.location.href = "copy:" + encodeURIComponent(text);
    return Promise.resolve();
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
    const next = el("div", "md-content", html);

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
        copyToClipboard(code ? code.textContent : "").then(() => {
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

  // ---- activity status (aggregate, spinner-driven — no per-call cards) ----
  // Instead of one card per tool call (which piled up into repeated rows of
  // the same tool name and required clicking to see file text), a single
  // line per turn accumulates counts by category and shows a spinner while
  // work is in progress. It freezes into a plain summary once the turn ends.
  const ACTIVITY = {
    read:     { progress: "Reading files",        past: "Read",     noun: "file" },
    edited:   { progress: "Editing files",         past: "Edited",   noun: "file" },
    searched: { progress: "Searching code",        past: "Searched", noun: "search" },
    ran:      { progress: "Running a command",     past: "Ran",      noun: "command" },
    fetched:  { progress: "Fetching the web",       past: "Fetched",  noun: "page" },
    memory:   { progress: "Updating memory",        past: "Updated",  noun: "memory note" },
    skills:   { progress: "Managing skills",        past: "Updated",  noun: "skill" },
    sessions: { progress: "Checking past sessions", past: "Checked",  noun: "session" },
    asked:    { progress: "Asking a question",      past: "Asked",    noun: "question" },
    other:    { progress: "Working",                past: "Used",     noun: "tool" },
  };
  const ACTIVITY_ORDER = ["read", "edited", "searched", "ran", "fetched", "memory", "skills", "sessions", "asked", "other"];

  function classifyTool(name, argsText) {
    let args = {};
    try { args = JSON.parse(argsText || "{}"); } catch {}
    if (name === "read_files") {
      const paths = args.paths;
      return { category: "read", count: Array.isArray(paths) ? paths.length || 1 : 1 };
    }
    if (name === "editor") {
      const op = (args.operation || "").toLowerCase();
      return { category: "edited", count: op === "move" ? 2 : 1 };
    }
    if (name === "search_codebase") return { category: "searched", count: 1 };
    if (name === "run_command") return { category: "ran", count: 1 };
    if (name === "fetch_web") return { category: "fetched", count: 1 };
    if (name === "memory") return { category: "memory", count: 1 };
    if (name === "skills") return { category: "skills", count: 1 };
    if (name === "sessions") return { category: "sessions", count: 1 };
    if (name === "ask_question") return { category: "asked", count: 1 };
    return { category: "other", count: 1 };
  }

  function formatCounts(counts) {
    const parts = [];
    for (const key of ACTIVITY_ORDER) {
      const n = counts[key];
      if (!n) continue;
      const a = ACTIVITY[key];
      parts.push(a.past + " " + n + " " + a.noun + (n === 1 ? "" : "s"));
    }
    return parts.join(" · ");
  }

  let activityItem = null; // {el, counts, verb, active} — one per turn

  function ensureActivityItem() {
    if (activityItem) return activityItem;
    const wrap = el("div", "item item-status");
    wrap.innerHTML = '<span class="spinner spin"></span><span class="verb"></span><span class="counts"></span>';
    activityItem = { el: wrap, counts: {}, active: true };
    append({ kind: "status", el: wrap });
    return activityItem;
  }

  function renderActivity(item) {
    const wrap = item.el;
    const spinner = wrap.querySelector(".spinner");
    const verbEl = wrap.querySelector(".verb");
    const countsEl = wrap.querySelector(".counts");
    const countsText = formatCounts(item.counts);
    if (item.active) {
      spinner.className = "spinner spin";
      verbEl.textContent = (item.verb || "Working") + "…";
    } else {
      spinner.className = "spinner done";
      verbEl.textContent = "";
    }
    countsEl.textContent = countsText;
  }

  // Renders an already-finished turn's activity line directly (used when
  // replaying a resumed session, where counts were aggregated in Python —
  // there are no live tool_start/tool_result events to accumulate from).
  function renderFrozenStatus(counts) {
    const wrap = el("div", "item item-status");
    wrap.innerHTML = '<span class="spinner spin"></span><span class="verb"></span><span class="counts"></span>';
    append({ kind: "status", el: wrap });
    renderActivity({ el: wrap, counts: counts || {}, active: false });
  }

  function finalizeActivity() {
    if (!activityItem) return;
    activityItem.active = false;
    renderActivity(activityItem);
    // Nothing actually happened (e.g. the turn was cut short) — drop the empty line.
    if (!formatCounts(activityItem.counts)) {
      activityItem.el.remove();
      const idx = items.findIndex((i) => i.el === activityItem.el);
      if (idx !== -1) items.splice(idx, 1);
    }
    activityItem = null;
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
      const label = pendingReasoning.querySelector(".label");
      if (label) label.innerHTML = '<b>Thinking</b>';
    }
    // The turn is over — freeze the activity line into its final summary.
    finalizeActivity();
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
        '<div class="label"></div>' +
        '<div class="body"></div>';
      append({ kind: "reasoning", el: pendingReasoning });
    }

    const label = pendingReasoning.querySelector(".label");
    const body = pendingReasoning.querySelector(".body");

    if (streaming) {
      label.innerHTML = '<span class="shimmer"></span> Thinking';
      const h = heading(ev.content);
      if (h) label.innerHTML += ' <span style="opacity:.7">' + escapeText(h) + '</span>';
      renderMarkdownInto(body, ev.content, false);
    } else {
      label.innerHTML = '<b>Thinking</b>';
      renderMarkdownInto(body, ev.content, true);
    }
  }

  function handleToolStart(ev) {
    const item = ensureActivityItem();
    item.verb = ACTIVITY[classifyTool(ev.name, ev.args).category].progress;
    item.active = true;
    renderActivity(item);
  }

  function handleToolResult(ev) {
    const item = ensureActivityItem();
    const cls = classifyTool(ev.name, ev.args);
    item.counts[cls.category] = (item.counts[cls.category] || 0) + cls.count;
    item.active = true;
    renderActivity(item);
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
  function resetTranscript() {
    items.length = 0;
    pendingAssistant = null;
    pendingReasoning = null;
    activityItem = null;
    // Clear reveal pacing state for GC; the WeakMap entries die with the DOM nodes.
    while (root.firstChild) root.removeChild(root.firstChild);
  }

  window.__appendEvent = function (jsonString) {
    let ev;
    try { ev = JSON.parse(jsonString); } catch { return; }
    const t = ev.type;

    if (t === "reset") { resetTranscript(); return; }
    if (t === "user") { finalizeActivity(); append({ kind: "user", el: renderUser(ev.text) }); return; }
    if (t === "error") { finalizeActivity(); append({ kind: "error", el: renderError(ev.text) }); return; }
    if (t === "done") { renderDone(); return; }
    if (t === "theme") { renderTheme(ev.mode); return; }
    if (t === "assistant_message") { handleAssistantMessage(ev); return; }
    if (t === "reasoning") { handleReasoning(ev); return; }
    if (t === "tool_start") { handleToolStart(ev); return; }
    if (t === "tool_result") { handleToolResult(ev); return; }
    if (t === "status") { renderFrozenStatus(ev.counts); return; }
    if (t === "diff") { handleDiff(ev); return; }
  };

  function append(item) {
    items.push(item);
    root.appendChild(item.el);
    window.scrollTo(0, root.scrollHeight);
  }
})();