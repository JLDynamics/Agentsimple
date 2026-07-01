# Opencode-Style Output Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Qt desktop chat transcript (`QTextBrowser`) with a `QWebEngineView` that renders opencode-style output: Shiki syntax highlighting, streaming markdown with paced reveal, tool cards, collapsible reasoning, and inline diffs.

**Architecture:** A `web/` folder of static assets (no Node/Vite build) loaded into `QWebEngineView`. A thin Python bridge converts `AgentWorker` signals to JSON events pushed via `runJavaScript()`. `run_agent_events()` gains a backward-compatible optional streaming callback. `marked` + `Shiki` (Web Worker) + `morphdom` are the same libraries opencode uses.

**Tech Stack:** PySide6 QtWebEngineWidgets, vanilla JS, marked v12, morphdom-umd v2, Shiki v1 (ESM in a Web Worker), Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-07-01-opencode-style-output-design.md`

---

## File Structure

**Create:**
- `web/index.html` — shell page, loads vendor libs + `app.js` + `styles.css`
- `web/vendor/marked.min.js` — vendored marked (single file)
- `web/vendor/morphdom-umd.min.js` — vendored morphdom (single file)
- `web/styles.css` — theme variables (light/dark), tool cards, code blocks, diffs
- `web/shiki.worker.js` — ES-module Web Worker running `shiki.codeToHtml`
- `web/app.js` — event dispatcher + renderers (~350 lines)
- `test_qt_bridge_behavior.py` — unit tests for the Python bridge logic

**Modify:**
- `agent_events.py` — add `on_assistant_chunk` / `on_reasoning_chunk` optional params
- `qt_app.py` — add `WebBridge` class; swap `QTextBrowser` → `QWebEngineView` in `ChatWindow`; rewire `add_*` slots; wire streaming callbacks in `AgentWorker`; add WebEngine-missing fallback
- `SimpleAgentNative.spec` — add `web/` to `datas`

---

### Task 1: Add streaming callbacks to `run_agent_events()`

**Files:**
- Modify: `agent_events.py`
- Test: `test_qt_bridge_behavior.py` (create)

This is the only change to the agent core, and it is backward compatible. When the callbacks are `None` (Chainlit, existing tests), behavior is identical to today.

- [ ] **Step 1: Write the failing test**

Create `test_qt_bridge_behavior.py`:

```python
"""Tests for the Qt bridge logic and the streaming parameter on run_agent_events."""
import json
import unittest
from unittest.mock import MagicMock, patch

from agent_events import run_agent_events


class StreamingParameterTests(unittest.TestCase):
    def test_signature_has_optional_callbacks(self):
        """run_agent_events must accept on_assistant_chunk and on_reasoning_chunk,
        both defaulting to None so existing callers are unaffected."""
        import inspect

        sig = inspect.signature(run_agent_events)
        params = sig.parameters
        self.assertIn("on_assistant_chunk", params)
        self.assertIn("on_reasoning_chunk", params)
        self.assertIsNone(params["on_assistant_chunk"].default)
        self.assertIsNone(params["on_reasoning_chunk"].default)

    def test_no_callbacks_uses_non_streaming_complete(self):
        """With callbacks=None, complete() is called with stream_messages=False
        (the existing behavior)."""
        client = MagicMock()
        with patch("agent_events.complete") as mock_complete:
            mock_complete.return_value = {
                "role": "assistant",
                "content": "hi",
                "tool_calls": None,
                "reasoning": None,
            }
            gen = run_agent_events(
                client, "m", [{"role": "user", "content": "hi"}],
                max_agent_steps=1, approval_mode="safe_auto",
            )
            list(gen)  # drain
            _, kwargs = mock_complete.call_args
            self.assertEqual(kwargs.get("stream_messages"), False)

    def test_callbacks_enable_streaming_and_accumulate(self):
        """When on_assistant_chunk is provided, complete() is called with
        stream_messages=True, on_chunk receives accumulated text, and the
        callback is invoked with the accumulated string (not the raw delta)."""
        client = MagicMock()
        chunks_received = []

        def fake_complete(client, model_name, messages, stream_messages,
                          tools=None, on_chunk=None, on_reasoning=None):
            self.assertTrue(stream_messages)
            # Simulate the stream emitting two deltas
            on_chunk("Hel")
            on_chunk("lo")
            if on_reasoning:
                on_reasoning("thinking...")
            return {
                "role": "assistant",
                "content": "Hello",
                "tool_calls": None,
                "reasoning": "thinking...",
            }

        def on_assistant_chunk(accumulated):
            chunks_received.append(accumulated)

        with patch("agent_events.complete", side_effect=fake_complete):
            gen = run_agent_events(
                client, "m", [{"role": "user", "content": "hi"}],
                max_agent_steps=1, approval_mode="safe_auto",
                on_assistant_chunk=on_assistant_chunk,
            )
            events = list(gen)

        # The callback must have been called with accumulated text, not raw deltas.
        self.assertEqual(chunks_received, ["Hel", "Hello"])
        # The generator must still yield the final assistant_message with streaming=False.
        assistant_events = [e for e in events if e["type"] == "assistant_message"]
        self.assertTrue(any(not e.get("streaming", False) for e in assistant_events))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest test_qt_bridge_behavior.py -v`
Expected: FAIL — `TypeError: run_agent_events() got an unexpected keyword argument 'on_assistant_chunk'`

- [ ] **Step 3: Implement the streaming callbacks**

Replace the signature and the `complete()` call in `agent_events.py`. The full updated function (lines 15-34 of `agent_events.py`):

```python
def run_agent_events(client, model_name, messages, max_agent_steps,
                     approval_mode, intent='', on_assistant_chunk=None,
                     on_reasoning_chunk=None):
    for step_number in range(1, max_agent_steps + 1):
        if on_assistant_chunk is not None or on_reasoning_chunk is not None:
            # Streaming path: accumulate deltas so the callbacks receive the
            # full text so far (not raw deltas), matching opencode's behavior.
            _acc_content = []
            _acc_reasoning = []

            def _chunk_cb(piece):
                _acc_content.append(piece)
                if on_assistant_chunk:
                    on_assistant_chunk("".join(_acc_content))

            def _reasoning_cb(piece):
                _acc_reasoning.append(piece)
                if on_reasoning_chunk:
                    on_reasoning_chunk("".join(_acc_reasoning))

            assistant_record = complete(
                client, model_name, messages, stream_messages=True,
                tools=TOOLS, on_chunk=_chunk_cb, on_reasoning=_reasoning_cb,
            )
        else:
            assistant_record = complete(
                client, model_name, messages, stream_messages=False, tools=TOOLS
            )

        content = assistant_record["content"]
        tool_calls = assistant_record["tool_calls"] or []
        reasoning = assistant_record.get("reasoning")

        if reasoning:
            # Streaming: incremental chunks were already pushed via the callback.
            # Non-streaming: no chunks were pushed, so this is the only event.
            # Either way, yield one final streaming=False marker so the UI can
            # stop the shimmer. (Yield exactly once — never double-yield.)
            yield {"type": "reasoning", "content": reasoning, "streaming": False}

        if content:
            if on_assistant_chunk is None:
                yield {"type": "assistant_message", "content": content}
            else:
                yield {
                    "type": "assistant_message",
                    "content": content,
                    "streaming": False,
                }

        if not tool_calls:
            messages.append({"role": "assistant", "content": content})
            yield {"type": "done"}
            return

        messages.append(assistant_record_for_history(assistant_record))

        denied = False

        for tool_call in tool_calls:
            name = get_tool_call_name(tool_call)
            args = get_tool_call_arguments(tool_call)

            yield {"type": "tool_start", "name": name, "args": args}

            run_mode = approval_mode

            if name == "run_command":
                try:
                    command = json.loads(args).get("command", "")
                except (json.JSONDecodeError, AttributeError):
                    command = ""

                decision = decide_command(command, approval_mode, intent=intent)

                if decision.action == "approval_required":
                    answer = yield {
                        "type": "approval_request",
                        "command": command,
                        "reason": decision.reason
                    }

                    if answer != "approve":
                        result = "CANCELLED: User did not approve the command."
                        yield {"type": "tool_result", "name": name, "args": args, "result": result}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": get_tool_call_id(tool_call),
                            "content": result,
                        })
                        denied = True
                        continue

                    run_mode = "full_auto"

            if name == "ask_question":
                try:
                    parsed = json.loads(args)
                    q_text = parsed.get("question", "")
                    q_opts = parsed.get("options", [])
                except (json.JSONDecodeError, AttributeError):
                    q_text = ""
                    q_opts = []

                answer = yield {
                    "type": "question_request",
                    "question": q_text,
                    "options": q_opts,
                }

                result = f"User chose: {answer}" if answer else "User did not answer."
                yield {"type": "tool_result", "name": name, "args": args, "result": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": get_tool_call_id(tool_call),
                    "content": result,
                })
                continue

            result = run_tool(name, args, False, run_mode)

            yield {"type": "tool_result", "name": name, "args": args, "result": result}

            messages.append({
                "role": "tool",
                "tool_call_id": get_tool_call_id(tool_call),
                "content": result,
            })

        if denied:
            messages.append({
                "role": "assistant",
                "content": "Stopped: you denied the command, so I will not continue this turn.",
            })
            yield {"type": "done"}
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest test_qt_bridge_behavior.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify existing events test still passes**

Run: `uv run python -m pytest test_events.py -v` (if it exists and is a unittest); otherwise `uv run python -m py_compile agent_events.py`
Expected: PASS / clean compile

- [ ] **Step 6: Commit**

```bash
git add agent_events.py test_qt_bridge_behavior.py
git commit -m "Add optional streaming callbacks to run_agent_events"
```

---

### Task 2: Vendor the web libraries

**Files:**
- Create: `web/vendor/marked.min.js`
- Create: `web/vendor/morphdom-umd.min.js`

These are single-file UMD bundles downloaded from the jsDelivr CDN (a reliable mirror of npm). They expose globals (`window.markdownit` is not used — `marked` exposes `window.marked`, `morphdom` exposes `window.morphdom`) so no build step is needed.

- [ ] **Step 1: Create the vendor directory and download marked**

Run from the project root:

```powershell
New-Item -ItemType Directory -Path "web\vendor" -Force | Out-Null
Invoke-WebRequest -Uri "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js" -OutFile "web\vendor\marked.min.js"
```

- [ ] **Step 2: Download morphdom**

```powershell
Invoke-WebRequest -Uri "https://cdn.jsdelivr.net/npm/morphdom@2.12.1/dist/morphdom-umd.min.js" -OutFile "web\vendor\morphdom-umd.min.js"
```

- [ ] **Step 3: Verify both files are non-trivial**

```powershell
(Get-Item "web\vendor\marked.min.js").Length
(Get-Item "web\vendor\morphdom-umd.min.js").Length
```
Expected: both > 10000 bytes.

- [ ] **Step 4: Commit**

```bash
git add web/vendor/marked.min.js web/vendor/morphdom-umd.min.js
git commit -m "Vendor marked and morphdom browser bundles"
```

> Note on Shiki: Shiki v1+ is ESM-only. It is loaded via an ES-module Web Worker in Task 5 from the esm.sh CDN, with a graceful sync fallback if the worker fails. Fully-offline vendoring of Shiki's ESM dist is a documented follow-up in Task 17 — it is intentionally deferred because the spec's fallback (plain `<pre><code>` when Shiki is unavailable) keeps the app working offline.

---

### Task 3: Create `web/index.html`

**Files:**
- Create: `web/index.html`

- [ ] **Step 1: Write the shell page**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SimpleAgent</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body data-theme="dark">
  <div id="transcript"></div>
  <script src="vendor/marked.min.js"></script>
  <script src="vendor/morphdom-umd.min.js"></script>
  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add web/index.html
git commit -m "Add web chat shell page"
```

---

### Task 4: Create `web/styles.css` (theme variables + component styles)

**Files:**
- Create: `web/styles.css`

This ports the existing `THEMES` dict from `qt_app.py` to CSS custom properties, plus styles for tool cards, code blocks, diffs, and the reasoning shimmer.

- [ ] **Step 1: Write the stylesheet**

```css
:root {
  --font-mono: "Cascadia Code", Consolas, "Courier New", monospace;
  --font-sans: -apple-system, "Segoe UI", Roboto, sans-serif;
}

body[data-theme="dark"] {
  --window-bg: #1b1b1f;
  --surface: #27272a;
  --border: #3f3f46;
  --text: #e5e7eb;
  --subtle: #71717a;
  --tool: #a1a1aa;
  --user: #60a5fa;
  --agent: #2dd4bf;
  --error: #f87171;
  --code-bg: #18181b;
  --code-fg: #e5e7eb;
  --inline-code: #f472b6;
  --diff-add-bg: #12361f;
  --diff-add-fg: #7ee2a8;
  --diff-del-bg: #3d1418;
  --diff-del-fg: #ff9da3;
  --diff-hunk: #a78bfa;
  --shiki-theme: github-dark;
}

body[data-theme="light"] {
  --window-bg: #f9fafb;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #1f2937;
  --subtle: #9ca3af;
  --tool: #6b7280;
  --user: #2563eb;
  --agent: #0f766e;
  --error: #b91c1c;
  --code-bg: #f3f4f6;
  --code-fg: #111827;
  --inline-code: #be185d;
  --diff-add-bg: #e6ffec;
  --diff-add-fg: #116329;
  --diff-del-bg: #ffebe9;
  --diff-del-fg: #82071e;
  --diff-hunk: #8250df;
  --shiki-theme: github-light;
}

body {
  margin: 0;
  padding: 16px 20px;
  background: var(--window-bg);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.55;
}

#transcript { max-width: 900px; margin: 0 auto; }

/* ---- item spacing ---- */
.item { margin-bottom: 18px; }

/* ---- user ---- */
.item-user { text-align: right; }
.item-user .bubble {
  display: inline-block; text-align: left;
  background: color-mix(in srgb, var(--user) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--user) 30%, transparent);
  color: var(--text);
  padding: 8px 12px; border-radius: 12px; max-width: 80%;
  white-space: pre-wrap; word-break: break-word;
}

/* ---- agent ---- */
.item-agent .label { color: var(--agent); font-weight: 700; margin-bottom: 4px; }

/* ---- markdown ---- */
.item-agent .md { line-height: 1.6; }
.item-agent .md p:first-child { margin-top: 0; }
.item-agent .md p:last-child { margin-bottom: 0; }
.item-agent .md code {
  font-family: var(--font-mono); font-size: 0.9em;
  background: var(--code-bg); color: var(--inline-code);
  padding: 1px 5px; border-radius: 4px;
}
.markdown-code {
  position: relative; margin: 10px 0;
  background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px;
}
.markdown-code pre { margin: 0; padding: 12px 14px; overflow-x: auto; }
.markdown-code code { font-family: var(--font-mono); font-size: 13px; line-height: 1.5; }
.markdown-code .copy-btn {
  position: absolute; top: 6px; right: 6px;
  background: var(--surface); border: 1px solid var(--border); color: var(--subtle);
  font-size: 11px; padding: 2px 8px; border-radius: 5px; cursor: pointer;
}
.markdown-code .copy-btn:hover { color: var(--text); }

/* ---- reasoning ---- */
.item-reasoning .toggle { color: var(--subtle); cursor: pointer; font-size: 12px; user-select: none; }
.item-reasoning .body { margin: 4px 0 0 18px; font-size: 12px; color: var(--subtle); }
.shimmer {
  display: inline-block; min-width: 60px; height: 12px; border-radius: 4px;
  background: linear-gradient(90deg, var(--surface) 25%, var(--border) 50%, var(--surface) 75%);
  background-size: 200% 100%; animation: shimmer 1.4s infinite;
}
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

/* ---- tool cards ---- */
.tool-card {
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
  padding: 8px 12px; font-size: 13px;
}
.tool-card .head { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.tool-card .icon { width: 16px; height: 16px; flex: 0 0 16px; color: var(--tool); }
.tool-card .title { color: var(--text); font-weight: 500; }
.tool-card .subtitle { color: var(--subtle); font-family: var(--font-mono); font-size: 12px; }
.tool-card .summary { color: var(--subtle); margin-left: auto; font-size: 12px; }
.tool-card .status { width: 14px; height: 14px; flex: 0 0 14px; }
.tool-card .status.pending { animation: spin 1s linear infinite; }
.tool-card .result {
  margin-top: 8px; padding: 8px; background: var(--code-bg); border-radius: 6px;
  font-family: var(--font-mono); font-size: 12px; white-space: pre-wrap;
  word-break: break-word; max-height: 320px; overflow: auto;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ---- diff ---- */
.diff-card .head { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 12px; }
.diff-card .path { color: var(--tool); font-weight: 500; }
.diff-card .added { color: var(--diff-add-fg); }
.diff-card .removed { color: var(--diff-del-fg); }
.diff-body {
  margin-top: 6px; background: var(--code-bg); border-radius: 6px; padding: 8px;
  font-family: var(--font-mono); font-size: 12px;
}
.diff-body .line { white-space: pre; }
.diff-body .line.add { background: var(--diff-add-bg); color: var(--diff-add-fg); }
.diff-body .line.del { background: var(--diff-del-bg); color: var(--diff-del-fg); }
.diff-body .line.hunk { color: var(--diff-hunk); font-weight: 700; }
.diff-body .line.meta { color: var(--subtle); }

/* ---- error ---- */
.item-error {
  border: 1px solid var(--error); color: var(--error);
  background: color-mix(in srgb, var(--error) 10%, transparent);
  padding: 8px 12px; border-radius: 8px;
}
```

- [ ] **Step 2: Commit**

```bash
git add web/styles.css
git commit -m "Add opencode-style theme variables and component CSS"
```

---

### Task 5: Create `web/shiki.worker.js` (ES-module Web Worker)

**Files:**
- Create: `web/shiki.worker.js`

The worker loads Shiki from esm.sh, builds a highlighter with a small set of common languages, and responds to `postMessage` requests with highlighted HTML. If the import fails, it posts an `error` so the main thread falls back to plain `<pre><code>`.

- [ ] **Step 1: Write the worker**

```js
// ES-module Web Worker. QWebEngineView (Chromium) supports `new Worker(url, {type:"module"})`.
let highlighter = null;
let ready = false;
let failed = false;

const LANGS = ["python", "javascript", "typescript", "bash", "json", "html", "css",
               "jsx", "tsx", "yaml", "markdown", "go", "rust", "java", "c", "cpp"];

async function init() {
  try {
    const { createHighlighter } = await import("https://esm.sh/shiki@1.29.2");
    highlighter = await createHighlighter({
      themes: ["github-light", "github-dark"],
      langs: LANGS,
    });
    ready = true;
    postMessage({ type: "ready" });
  } catch (err) {
    failed = true;
    postMessage({ type: "error", message: String(err) });
  }
}

onmessage = (e) => {
  const { id, code, lang, theme } = e.data;
  if (failed) {
    postMessage({ id, type: "error", message: "worker failed to init" });
    return;
  }
  if (!ready) {
    // Queue: retry once ready. The main thread handles a missing response.
    postMessage({ id, type: "pending" });
    return;
  }
  try {
    const realLang = LANGS.includes(lang) ? lang : "markdown";
    const html = highlighter.codeToHtml(code, { lang: realLang, theme });
    postMessage({ id, type: "ok", html });
  } catch (err) {
    postMessage({ id, type: "error", message: String(err) });
  }
};

init();
```

- [ ] **Step 2: Commit**

```bash
git add web/shiki.worker.js
git commit -m "Add Shiki Web Worker for off-main-thread syntax highlighting"
```

---

### Task 6: Create `web/app.js` — dispatcher, state, and simple renderers

**Files:**
- Create: `web/app.js`

This task creates the module skeleton: the `items` state, the `__appendEvent` dispatcher, and the simple renderers (`user`, `error`, `done`, `theme`). The markdown pipeline, streaming, tool cards, reasoning, and diffs are added in subsequent tasks by appending functions.

- [ ] **Step 1: Write the skeleton**

```js
// app.js — opencode-style chat renderer for SimpleAgent's QWebEngineView.
// No framework. Receives JSON events pushed from Python via window.__appendEvent.

(function () {
  "use strict";

  const root = document.getElementById("transcript");
  const items = [];          // {el, kind, ...} — mirrors Python's self.items
  let pendingAssistant = null; // the currently-streaming message element
  let pendingTool = null;      // the most recent tool_start card (awaiting its result)

  // ---- markdown defaults (overridden in Task 7) ----
  function mdToHtml(text) {
    // Fallback: escape + <br>. Replaced by Task 7 with marked + Shiki.
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return esc(text || "").replace(/\n/g, "<br>");
  }

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

  // ---- renderers (simple ones here; complex ones added in later tasks) ----
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
    return null;
  }

  function renderTheme(mode) {
    document.body.dataset.theme = mode === "light" ? "light" : "dark";
    return null;
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

    // The following renderers are implemented in Tasks 7-11. They are referenced
    // here so the dispatcher is complete; the functions are defined later.
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

  // Placeholders implemented in later tasks. Defining no-op stubs keeps the
  // dispatcher runnable; they are overwritten when each task appends its code.
  function handleAssistantMessage(ev) {
    if (pendingAssistant == null) {
      pendingAssistant = el("div", "item item-agent streaming");
      pendingAssistant.innerHTML = '<div class="label">Agent</div><div class="md"></div>';
      append({ kind: "agent", el: pendingAssistant });
    }
    const md = pendingAssistant.querySelector(".md");
    if (md) md.innerHTML = mdToHtml(ev.content);
    if (!ev.streaming) { pendingAssistant.classList.remove("streaming"); pendingAssistant = null; }
  }
  function handleReasoning(ev) {}
  function handleToolStart(ev) {}
  function handleToolResult(ev) {}
  function handleDiff(ev) {}
})();
```

- [ ] **Step 2: Commit**

```bash
git add web/app.js
git commit -m "Add app.js skeleton: dispatcher, state, and simple renderers"
```

---

### Task 7: Markdown pipeline (marked + Shiki worker + copy buttons + morphdom)

**Files:**
- Modify: `web/app.js`

Replace the `mdToHtml` fallback with the real pipeline: parse with `marked`, send each `<pre><code>` to the Shiki worker, wrap with a copy button, and patch the container with `morphdom`. The worker is async; we render a placeholder `<pre>` immediately and swap in highlighted HTML when the worker responds.

- [ ] **Step 1: Replace the `mdToHtml` function and add the pipeline**

In `web/app.js`, replace the `mdToHtml` fallback (the block under `// ---- markdown defaults ----`) with:

```js
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
        if (d.type === "ok" && req.pre) {
          // Swap the placeholder pre's innerHTML with Shiki's highlighted output.
          req.pre.innerHTML = d.html;
        }
      };
    } catch {
      shikiFailed = true;
    }
  }

  function flushPendingShiki() {
    if (shikiReady) {
      for (const [id, req] of shikiPending) {
        const theme = getComputedStyle(document.body).getPropertyValue("--shiki-theme").trim() || "github-dark";
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
      const theme = getComputedStyle(document.body).getPropertyValue("--shiki-theme").trim() || "github-dark";
      shikiWorker.postMessage({ id, code, lang, theme });
    }
  }

  function currentShikiTheme() {
    return getComputedStyle(document.body).getPropertyValue("--shiki-theme").trim() || "github-dark";
  }

  // Re-highlight all code blocks on theme change (called from renderTheme).
  function rehighlightForTheme() {
    if (!shikiReady) return;
    root.querySelectorAll(".markdown-code pre").forEach((pre) => {
      const code = pre.querySelector("code");
      if (!code) return;
      const lang = (code.className.match(/language-([\w-]+)/) || [])[1] || "text";
      requestHighlight(pre, code.textContent, lang);
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
  function renderMarkdownInto(container, text) {
    const html = mdToHtml(text);
    const next = el("div", "", html);

    // Decorate: wrap each <pre> in .markdown-code with a copy button.
    next.querySelectorAll("pre").forEach((pre) => {
      const code = pre.querySelector("code");
      const lang = code ? (code.className.match(/language-([\w-]+)/) || [])[1] : null;
      const wrapper = el("div", "markdown-code");
      pre.parentElement.replaceChild(wrapper, pre);
      wrapper.appendChild(pre);
      const btn = el("button", "copy-btn", "Copy");
      btn.addEventListener("click", () => {
        navigator.clipboard.writeText(code ? code.textContent : "").then(() => {
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 2000);
        });
      });
      wrapper.appendChild(btn);
      if (code) requestHighlight(pre, code.textContent, lang || "text");
    });

    if (morphdomLib) {
      morphdomLib(container, next, {
        onBeforeElUpdated: (fromEl, toEl) => {
          // Preserve copy buttons and already-highlighted <pre> across patches.
          if (fromEl.dataset && fromEl.dataset.slot === "markdown-copy-button") return false;
          return true;
        },
      });
    } else {
      container.innerHTML = next.innerHTML;
    }
  }
```

- [ ] **Step 2: Wire the pipeline into `handleAssistantMessage`**

Replace the `handleAssistantMessage` stub with:

```js
  function handleAssistantMessage(ev) {
    if (pendingAssistant == null) {
      pendingAssistant = el("div", "item item-agent streaming");
      pendingAssistant.innerHTML = '<div class="label">Agent</div><div class="md"></div>';
      append({ kind: "agent", el: pendingAssistant });
    }
    const md = pendingAssistant.querySelector(".md");
    if (md) renderMarkdownInto(md, ev.content);
    if (!ev.streaming) {
      pendingAssistant.classList.remove("streaming");
      pendingAssistant = null;
    }
  }
```

- [ ] **Step 3: Wire `renderTheme` to re-highlight**

Replace the `renderTheme` function with:

```js
  function renderTheme(mode) {
    document.body.dataset.theme = mode === "light" ? "light" : "dark";
    rehighlightForTheme();
    return null;
  }
```

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "Add markdown pipeline: marked + Shiki worker + copy buttons + morphdom"
```

---

### Task 8: Paced reveal streaming

**Files:**
- Modify: `web/app.js`

Port opencode's `createPacedValue` / `PacedMarkdown`: reveal streamed text at 24ms intervals with a 512-char immediate threshold, so long answers animate in rather than dumping at once.

- [ ] **Step 1: Add the pacer and a per-message reveal state**

In `web/app.js`, add (before `handleAssistantMessage`):

```js
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
    const sync = (t) => { st.shown = t; renderMarkdownInto(container, t); };

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
```

- [ ] **Step 2: Use `pacedRender` in `handleAssistantMessage`**

Update `handleAssistantMessage` (from Task 7) to call `pacedRender` instead of `renderMarkdownInto`:

```js
  function handleAssistantMessage(ev) {
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
```

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "Add paced text reveal for streaming assistant messages"
```

---

### Task 9: Tool cards (`tool_start` + `tool_result`)

**Files:**
- Modify: `web/app.js`

A collapsible card per tool call: icon + `name(args)` + subtitle + status (pending spinner / completed checkmark) + expandable result. `tool_start` creates the pending card; `tool_result` updates it to completed (matched by name + order).

- [ ] **Step 1: Add the icon map and tool-card helpers**

In `web/app.js`, add (before `handleToolStart`):

```js
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
    if (state === "pending") return '<svg class="status pending" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 000 20 10 10 0 000-20zM12 6v6l4 2"/></svg>';
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
```

- [ ] **Step 2: Implement `handleToolStart` and `handleToolResult`**

Replace the two stubs:

```js
  function handleToolStart(ev) {
    const card = buildToolCard(ev);
    pendingTool = { name: ev.name, card };
    append({ kind: "tool", el: card });
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
    if (!target) { target = { card: buildToolCard(ev) }; append({ kind: "tool", el: target.card }); }

    const card = target.card;
    const summary = card.querySelector(".summary");
    if (summary) summary.textContent = resultSummary(ev.result);
    const status = card.querySelector(".status");
    if (status) status.outerHTML = statusIcon("completed");
    if (card._result) {
      card._result.textContent = (ev.result || "").slice(0, 4000);
    }
  }
```

- [ ] **Step 3: Store the tool name on the item for matching**

Update `handleToolStart` to store the name:

```js
  function handleToolStart(ev) {
    const card = buildToolCard(ev);
    pendingTool = { name: ev.name, card };
    append({ kind: "tool", name: ev.name, el: card });
  }
```

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "Add collapsible tool cards for tool_start and tool_result"
```

---

### Task 10: Reasoning block with shimmer

**Files:**
- Modify: `web/app.js`

A collapsible "Thinking" block. While `streaming:true`: shimmer on the label + live markdown body. On `streaming:false`: stop shimmer, dim body. When a final `assistant_message` arrives, drop the preceding reasoning block (matches the current `add_agent` behavior).

- [ ] **Step 1: Implement `handleReasoning`**

Replace the `handleReasoning` stub:

```js
  let pendingReasoning = null;

  function heading(text) {
    const m = (text || "").match(/^\s{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$/m);
    if (m && m[1]) return m[1].replace(/[*_`~]/g, "").trim();
    const s = (text || "").match(/^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*$/m);
    if (s && s[1]) return s[1].trim();
    return "";
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

    const toggle = pendingReasoning.querySelector(".toggle");
    const body = pendingReasoning.querySelector(".body");

    if (streaming) {
      toggle.innerHTML = '<span class="shimmer"></span> Thinking';
      const h = heading(ev.content);
      if (h) toggle.innerHTML += ' <span style="opacity:.7">' + escapeText(h) + '</span>';
      if (pendingReasoning._expanded) renderMarkdownInto(body, ev.content);
    } else {
      toggle.innerHTML = (pendingReasoning._expanded ? "▼" : "▶") + ' <b>Thinking</b>';
      if (pendingReasoning._expanded) renderMarkdownInto(body, ev.content);
    }

    function updateToggleArrow() {
      if (!streaming) {
        toggle.innerHTML = (pendingReasoning._expanded ? "▼" : "▶") + ' <b>Thinking</b>';
      }
    }
  }
```

- [ ] **Step 2: Drop reasoning when the final assistant message arrives**

Update `handleAssistantMessage` to clear `pendingReasoning`:

```js
  function handleAssistantMessage(ev) {
    // Drop any reasoning blocks shown while thinking, so the final transcript
    // is clean — only the answer remains (matches the existing add_agent behavior).
    if (pendingReasoning) {
      pendingReasoning.remove();
      pendingReasoning = null;
      // Also remove it from the items list.
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
```

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "Add collapsible reasoning block with shimmer and heading preview"
```

---

### Task 11: Inline diffs

**Files:**
- Modify: `web/app.js`

A collapsible diff card: header = path + `+added −removed`; body = per-line colored lines (green/red/purple hunk headers). The `diff` event already carries `added`/`removed`/`diff` (Python computes the counts via the existing `diff_stats`).

- [ ] **Step 1: Implement `handleDiff`**

Replace the `handleDiff` stub:

```js
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
```

- [ ] **Step 2: Commit**

```bash
git add web/app.js
git commit -m "Add collapsible inline diffs with colored lines"
```

---

### Task 12: Python `WebBridge` class + tests

**Files:**
- Modify: `qt_app.py`
- Test: `test_qt_bridge_behavior.py`

The bridge owns the `QWebEngineView`, exposes `push(event_dict)` that serializes to JSON and calls `runJavaScript`, and computes the `added`/`removed` counts for diff events. The serialization logic is split into a pure function (`serialize_event`) so it is unit-testable without a live view.

- [ ] **Step 1: Write the failing tests**

Append to `test_qt_bridge_behavior.py`:

```python
from qt_app import serialize_event, diff_counts


class SerializeEventTests(unittest.TestCase):
    def test_user_event(self):
        self.assertEqual(
            serialize_event({"type": "user", "text": "hi"}),
            json.dumps({"type": "user", "text": "hi"}),
        )

    def test_assistant_message_streaming(self):
        out = json.loads(serialize_event({"type": "assistant_message", "content": "x", "streaming": True}))
        self.assertTrue(out["streaming"])

    def test_diff_includes_counts(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n+extra\n"
        out = json.loads(serialize_event({
            "type": "diff", "path": "foo.py", "diff": diff,
        }))
        self.assertEqual(out["added"], 2)
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["path"], "foo.py")

    def test_theme_event(self):
        out = json.loads(serialize_event({"type": "theme", "mode": "light"}))
        self.assertEqual(out["mode"], "light")

    def test_done_event(self):
        self.assertEqual(serialize_event({"type": "done"}), json.dumps({"type": "done"}))


class DiffCountsTests(unittest.TestCase):
    def test_counts_skip_file_headers(self):
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(diff_counts(diff), (1, 1))

    def test_empty(self):
        self.assertEqual(diff_counts(""), (0, 0))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest test_qt_bridge_behavior.py -v`
Expected: FAIL — `ImportError: cannot import name 'serialize_event'` from `qt_app`.

- [ ] **Step 3: Add `serialize_event`, `diff_counts`, and the `WebBridge` class to `qt_app.py`**

Add near the other module-level helpers in `qt_app.py` (after `result_summary`, ~line 137):

```python
def diff_counts(diff_text):
    """Count added / removed lines, skipping the +++ / --- file headers."""
    added = removed = 0
    for line in (diff_text or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def serialize_event(event):
    """Convert an agent event dict to a JSON string the web app expects.

    For 'diff' events, compute and inject added/removed counts (so the web
    side doesn't have to). Returns a JSON string ready for runJavaScript().
    """
    evt = dict(event)
    if evt.get("type") == "diff":
        added, removed = diff_counts(evt.get("diff", ""))
        evt["added"] = added
        evt["removed"] = removed
    return json.dumps(evt, ensure_ascii=False)
```

Add the `WebBridge` class after `serialize_event`:

```python
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtCore import QUrl

    _HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    _HAS_WEBENGINE = False


class WebBridge:
    """Owns the QWebEngineView chat transcript and pushes JSON events to it.

    Lazily imports QtWebEngineWidgets so the app still runs (falling back to
    QTextBrowser) when WebEngine is not installed.
    """

    def __init__(self, parent_widget, web_dir):
        self.view = QWebEngineView(parent_widget)
        self.view.load(QUrl.fromLocalFile(os.path.join(web_dir, "index.html")))

    def push(self, event):
        """Serialize one event and push it to the web page's __appendEvent."""
        js = "window.__appendEvent && window.__appendEvent(%s);" % serialize_event(event)
        self.view.page().runJavaScript(js)

    def widget(self):
        return self.view
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest test_qt_bridge_behavior.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add qt_app.py test_qt_bridge_behavior.py
git commit -m "Add WebBridge class and event serialization with tests"
```

---

### Task 13: Swap `QTextBrowser` → `QWebEngineView` and rewire slots

**Files:**
- Modify: `qt_app.py` (`_build_chat_panel`, `render`, `add_*` slots, `apply_theme`, `on_anchor`)

Replace the transcript widget with the bridge and rewire every `add_*` slot to `bridge.push(...)`. The old `render_item` / `render` / `diff_body_html` methods are kept only for the fallback path (Task 15) but are no longer called when WebEngine is present.

- [ ] **Step 1: Determine the `web/` directory path**

Add a module-level constant near the top of `qt_app.py` (after the imports, ~line 82):

```python
# Folder of static web assets (sibling to this file).
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_ASSETS = os.path.join(WEB_DIR, "web")
```

- [ ] **Step 2: Replace the transcript widget in `_build_chat_panel`**

Find `_build_chat_panel` (around line 902) and replace the `QTextBrowser` construction. The method currently does:

```python
    def _build_chat_panel(self):
        self.transcript = QTextBrowser()
        ...
```

Replace with a branch that uses the bridge when WebEngine is available, else the legacy `QTextBrowser`:

```python
    def _build_chat_panel(self):
        self.using_web = _HAS_WEBENGINE
        if self.using_web:
            self.bridge = WebBridge(self, WEB_ASSETS)
            self.transcript = self.bridge.widget()
        else:
            self.bridge = None
            self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        ...
```

Keep the rest of `_build_chat_panel` (layout, input box, send button) unchanged — it operates on `self.transcript` as a generic widget.

- [ ] **Step 3: Rewire the `add_*` slots to push events**

Replace the `add_user`, `add_agent`, `add_reasoning`, `add_tool`, `add_diff`, `add_error` methods (around lines 1744-1769) with versions that route to the bridge when present, and fall back to the legacy `items`/`render` path otherwise:

```python
    def add_user(self, text):
        if self.bridge:
            self.bridge.push({"type": "user", "text": text})
        else:
            self.add({"kind": "user", "text": text})

    def add_reasoning(self, text):
        if self.bridge:
            self.bridge.push({"type": "reasoning", "content": text, "streaming": False})
        else:
            self.add({"kind": "reasoning", "text": text, "expanded": True})

    def add_agent(self, text):
        if self.bridge:
            # Drop reasoning blocks handled by the web app itself.
            self.bridge.push({"type": "assistant_message", "content": text, "streaming": False})
        else:
            self.items = [i for i in self.items if i.get("kind") != "reasoning"]
            self.add({"kind": "agent", "text": text})

    def add_tool(self, name, args, result):
        if self.bridge:
            self.bridge.push({"type": "tool_result", "name": name, "args": args, "result": result})
        else:
            self.add({"kind": "tool", "name": name, "args": args, "result": result})

    def add_diff(self, path, diff_text):
        if self.bridge:
            self.bridge.push({"type": "diff", "path": path, "diff": diff_text})
        else:
            self.add({"kind": "diff", "path": path, "diff": diff_text})

    def add_error(self, text):
        if self.bridge:
            self.bridge.push({"type": "error", "text": text})
        else:
            self.add({"kind": "error", "text": text})
```

- [ ] **Step 4: Add a `toolStart` slot for pending tool cards**

The `AgentWorker.toolStart` signal currently only updates the status bar. Add a slot so the web app gets a `tool_start` event (creating the pending card before the result arrives). In `on_send`, after connecting the signals, add:

```python
        self.worker.toolStart.connect(self.add_tool_start)
```

And add the method:

```python
    def add_tool_start(self, name):
        if self.bridge:
            self.bridge.push({"type": "tool_start", "name": name, "args": ""})
        self.statusBar().showMessage(f"Running {name}...")
```

Note: `toolStart` carries only the name (no args) in the current signal. The web card shows `name()` with no subtitle; that is acceptable. If args are wanted later, widen the signal to `Signal(str, str)`.

- [ ] **Step 5: Push `done` and `theme` events**

In `on_finished`, push a `done` event before the existing finalization:

```python
    def on_finished(self):
        if self.bridge:
            self.bridge.push({"type": "done"})
        self.set_busy(False)
        self.worker = None
        self.statusBar().showMessage("Ready")
        self.save_current_session()
        self.refresh_sessions()
```

In `apply_theme` (after the existing styling), push the theme mode:

```python
        if self.bridge:
            self.bridge.push({"type": "theme", "mode": self.resolve_mode()})
```

- [ ] **Step 6: Compile-check**

Run: `uv run python -m py_compile qt_app.py`
Expected: clean (no output).

- [ ] **Step 7: Commit**

```bash
git add qt_app.py
git commit -m "Swap chat transcript to QWebEngineView and rewire slots to the bridge"
```

---

### Task 14: Wire streaming callbacks in `AgentWorker`

**Files:**
- Modify: `qt_app.py` (`AgentWorker.__init__`, `AgentWorker.run`, `ChatWindow.on_send`)

Pass `on_assistant_chunk` / `on_reasoning_chunk` callbacks into `run_agent_events` so the Qt app streams. The callbacks must emit to the bridge on the GUI thread — Qt signals are thread-safe, so the callback emits a signal whose slot pushes to the bridge.

- [ ] **Step 1: Add two streaming signals to `AgentWorker`**

In the `AgentWorker` signal declarations (around line 290), add:

```python
    assistantChunk = Signal(str)    # accumulated assistant text so far
    reasoningChunk = Signal(str)    # accumulated reasoning text so far
```

- [ ] **Step 2: Pass the callbacks into `run_agent_events` in `run()`**

In `AgentWorker.run()`, update the `run_agent_events(...)` call (around line 348) to pass streaming callbacks that emit the signals:

```python
            def on_assistant_chunk(accumulated):
                self.assistantChunk.emit(accumulated)

            def on_reasoning_chunk(accumulated):
                self.reasoningChunk.emit(accumulated)

            gen = run_agent_events(
                self.client,
                self.model,
                self.messages,
                int(self.config["max_agent_steps"]),
                self.config["approval_mode"],
                intent=self.user_text,
                on_assistant_chunk=on_assistant_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
            )
```

- [ ] **Step 3: Connect the streaming signals in `on_send`**

In `ChatWindow.on_send`, after the other signal connections, add:

```python
        self.worker.assistantChunk.connect(self.add_assistant_chunk)
        self.worker.reasoningChunk.connect(self.add_reasoning_chunk)
```

And add the slot (near `add_agent`):

```python
    def add_assistant_chunk(self, accumulated):
        if self.bridge:
            self.bridge.push({"type": "assistant_message", "content": accumulated, "streaming": True})

    def add_reasoning_chunk(self, accumulated):
        if self.bridge:
            self.bridge.push({"type": "reasoning", "content": accumulated, "streaming": True})
```

- [ ] **Step 4: Compile-check**

Run: `uv run python -m py_compile qt_app.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add qt_app.py
git commit -m "Wire streaming callbacks through AgentWorker signals to the bridge"
```

---

### Task 15: WebEngine-missing fallback to `QTextBrowser`

**Files:**
- Modify: `qt_app.py`

When `PySide6.QtWebEngineWidgets` is not installed, the app must still run with the existing `QTextBrowser` + `render_item` path. Task 13 already branches on `self.using_web`; this task verifies the legacy path is intact and the bridge calls are all guarded.

- [ ] **Step 1: Verify the legacy `render`/`render_item`/`diff_body_html` methods are still present**

Confirm these methods (lines ~1404-1443 and ~1646-1742) are unchanged and still referenced by the `else` branches of the `add_*` slots. No code change needed if Task 13 kept them.

- [ ] **Step 2: Verify `on_anchor` only runs for the legacy path**

`on_anchor` is wired to `QTextBrowser.anchorClicked`. With `QWebEngineView` there are no anchor signals (the web app handles its own clicks). Guard the connection in `_build_chat_panel`:

```python
        if not self.using_web:
            self.transcript.anchorClicked.connect(self.on_anchor)
```

- [ ] **Step 3: Compile-check**

Run: `uv run python -m py_compile qt_app.py`
Expected: clean.

- [ ] **Step 4: Commit (if changes were needed)**

```bash
git add qt_app.py
git commit -m "Guard legacy QTextBrowser path for when WebEngine is unavailable"
```

If no changes were needed because Task 13 already handled it, skip this commit.

---

### Task 16: PyInstaller spec — include `web/`

**Files:**
- Modify: `SimpleAgentNative.spec`

The spec file was deleted in the working tree (per `git status`), so this task recreates it with the `web/` data added. If the spec is restored from git first, just add the datas entry.

- [ ] **Step 1: Check the spec file state**

```powershell
if (Test-Path "SimpleAgentNative.spec") { Get-Content SimpleAgentNative.spec | Select-Object -First 40 } else { Write-Output "spec missing" }
```

- [ ] **Step 2: Add `web/` to the spec's `datas`**

In `SimpleAgentNative.spec`, ensure the `datas` list of the `Analysis` includes the web assets:

```python
datas=[
    ("tools_schema.json", "."),
    ("web", "web"),
],
```

If the file was deleted, recreate a minimal spec based on the prior `SimpleAgentNative.spec` from git history (`git show HEAD:SimpleAgentNative.spec`) plus the `web` entry above.

- [ ] **Step 3: Commit**

```bash
git add SimpleAgentNative.spec
git commit -m "Include web/ assets in the PyInstaller build"
```

---

### Task 17: Final verification

**Files:** none (verification only)

Follow the verification workflow from `CLAUDE.md`: compile with real Windows Python, then launch via the Run dialog with the venv on `PYTHONPATH`.

- [ ] **Step 1: Compile-check all changed Python**

```powershell
uv run python -m py_compile qt_app.py agent_events.py 2> _compile.txt
Get-Content _compile.txt
```
Expected: `_compile.txt` is empty (clean compile).

- [ ] **Step 2: Run the full Python test suite**

```powershell
uv run python -m pytest test_qt_bridge_behavior.py test_main_behavior.py test_tools_behavior.py test_safety_behavior.py -v
```
Expected: all tests PASS.

- [ ] **Step 3: Launch the app and run the visual checklist**

Launch `qt_app.py` (via Run dialog with `PYTHONPATH` set to the venv). Send a test prompt that triggers tool use and code generation. Confirm each item:

- [ ] Streaming markdown: assistant text appears incrementally (paced), not all at once.
- [ ] Syntax highlighting: fenced code blocks have Shiki colors; a copy button appears on each block and copies to clipboard ("Copied" 2s).
- [ ] Tool cards: a pending card (spinner) appears, then updates to completed (checkmark + summary) when the result arrives; clicking expands the result.
- [ ] Reasoning: a "Thinking" block with a shimmer appears while reasoning streams; it is dropped when the final answer arrives.
- [ ] Inline diff: after an `editor` edit, a collapsible diff card with green/red/purple lines and `+added −removed` counts appears.
- [ ] Theme: toggling light/dark updates the chat (and code highlighting) live.
- [ ] Fallback: temporarily rename `web/` and relaunch — app falls back to `QTextBrowser` and still works. Restore `web/`.

- [ ] **Step 4: Clean up throwaway verification artifacts**

Flag (do not auto-delete) for the user to remove:

```
_compile.txt
```

- [ ] **Step 5: Commit any remaining fixes (if the checklist surfaced bugs)**

If bugs were found and fixed during the checklist, commit them:

```bash
git add -A
git commit -m "Fix verification findings in opencode-style output"
```

---

## Notes

- **Shiki offline vendoring (follow-up):** This plan loads Shiki from the esm.sh CDN inside the Web Worker, with a graceful plain-`<pre>` fallback when offline. For fully-offline PyInstaller builds, a follow-up task should vendor the Shiki ESM `dist/` tree locally (e.g., `npm pack shiki@1.29.2`, extract `dist/` to `web/vendor/shiki/`, and change the worker's `import` to a relative path). The fallback keeps the app functional until that is done.
- **No JS unit tests:** Per the approved spec, the web rendering is verified manually. The Python bridge logic (`serialize_event`, `diff_counts`) is unit-tested.
- **`run_agent_events` backward compatibility:** The new callbacks default to `None`, so `chainlit_app.py`, `test_events.py`, and any other caller are unaffected.
