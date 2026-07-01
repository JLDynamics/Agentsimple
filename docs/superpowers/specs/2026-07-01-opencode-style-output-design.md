# Opencode-Style Output Rendering Design

Date: 2026-07-01

## Goal

Bring opencode's chat output rendering quality to the Qt desktop frontend
(`qt_app.py`): syntax-highlighted code, streaming markdown, styled tool cards,
collapsible reasoning, inline diffs, and paced text reveal.

The change is scoped to the **chat transcript only**. Everything outside the
transcript (left session panel, right file tree/editor, title bar, dialogs)
stays native Qt. The agent core (`agent_events.py`, `agent.py`, `llm.py`,
`tools.py`, `safety.py`, `sessions.py`) is unchanged except for one
backward-compatible optional parameter.

## Approach

Replace the `QTextBrowser` transcript with a `QWebEngineView` (Chromium) that
loads a small set of vendored web assets — no Node/Vite build step. The web
rendering uses the **same libraries opencode uses**: `marked` (markdown parse),
`Shiki` (TextMate syntax highlighting, in a Web Worker), and `morphdom`
(minimal DOM diffing for streaming). A thin Python bridge converts the existing
`AgentWorker` signals to JSON events and pushes them to the web view.

This was chosen over (a) porting opencode's Solid.js stack (adds a TS/Vite
build pipeline to a Python project; components are coupled to opencode's SDK
types) and (b) server-side Pygments rendering (no streaming; not what opencode
uses). Approach A uses opencode's actual rendering libraries without the
framework/build overhead.

## Scope

Implement:

1. A `web/` folder of static assets (no build step): `index.html`, vendored
   `marked`/`morphdom`/`shiki`, `app.js`, `styles.css`.
2. A Python bridge class in `qt_app.py` that owns the `QWebEngineView` and
   pushes JSON events via `page().runJavaScript()`.
3. Swap the `ChatWindow` transcript from `QTextBrowser` to `QWebEngineView`;
   rewire the `add_*` slots to the bridge.
4. Streaming support: an optional `on_assistant_chunk` callback parameter on
   `run_agent_events()` (default `None`, backward compatible).
5. Opencode-style renderers in `app.js`: markdown + Shiki + copy buttons,
   streaming with paced reveal + morphdom, tool cards, reasoning block with
   shimmer, inline diffs, theme sync.
6. Graceful fallback to the current `QTextBrowser` path when WebEngine is not
   installed, and to plain rendering when a vendored JS lib fails to load.
7. PyInstaller packaging: include `web/` in `SimpleAgentNative.spec`.
8. Python unit tests for the bridge logic; manual visual verification for the
   web rendering.

Out of scope:

- Changes to the CLI (`main.py`/`ui.py`) or Chainlit (`chainlit_app.py`)
  frontends. The bridge and streaming parameter don't break them, but they
  are not upgraded in this pass.
- A JavaScript unit-test framework.
- New model-provider or tool abstractions.
- Changes to the agent prompt, tool list, or session storage format.

## Design

### Architecture & file layout

The chat transcript swaps from `QTextBrowser` (Qt rich-text, HTML4/CSS2 subset)
to a `QWebEngineView` (Chromium) loading vendored web assets. Only the central
chat area becomes a web view; the rest of `ChatWindow` stays native Qt.

```
qt_app.py                     # modified: swap QTextBrowser -> QWebEngineView; add bridge; rewire slots
web/
├── index.html                # shell: <div id="transcript">, loads scripts
├── vendor/
│   ├── marked.min.js         # markdown parser (single file)
│   ├── morphdom.min.js       # minimal DOM diffing
│   └── shiki/                # Shiki browser bundle (pre-built, grammars + themes)
├── app.js                    # rendering glue (~350 lines): event dispatcher + renderers
├── styles.css                # opencode-style theming via CSS variables (light/dark)
└── shiki.worker.js           # Web Worker: runs codeToHtml off the main thread
```

What stays the same: `agent_events.py`, `agent.py`, `llm.py`, `tools.py`,
`safety.py`, `sessions.py`. The `AgentWorker` QThread and its signals stay;
only the `ChatWindow` slots that currently call `add_agent`/`add_tool`/
`add_diff` get rewired to `bridge.push_event(...)` instead of
`QTextBrowser.setHtml()`.

### Event bridge & data flow

The bridge is one-directional push (Python -> JS). `AgentWorker` emits signals
as today; `ChatWindow` slots convert each to a JSON event and call:

```python
self.view.page().runJavaScript(f"window.__appendEvent({json_str});")
```

No `QWebChannel` — that is heavier and only warranted for two-way calls.
Expand/collapse, copy buttons, and link clicks are handled natively in JS (no
Python round-trip). Approval/question flows stay as native `QMessageBox`
(unchanged), with the answer returned to `AgentWorker.provide_answer()`.

Event schema (the Python->JS contract, mirroring `agent_events.py` types):

```json
{"type": "user",        "text": "..."}
{"type": "reasoning",   "content": "...", "streaming": false}
{"type": "assistant_message", "content": "...", "streaming": false}
{"type": "tool_start",  "name": "editor", "args": "{...}"}
{"type": "tool_result", "name": "editor", "args": "{...}", "result": "..."}
{"type": "diff",        "path": "foo.py", "added": 5, "removed": 2, "diff": "..."}
{"type": "error",       "text": "..."}
{"type": "done"}
{"type": "theme",       "mode": "dark"}
```

`tool_start` creates a card in "pending" state; `tool_result` updates that
card to "completed" (matched by `name` + order). `diff` is emitted by
`AgentWorker._emit_file_diff()` as today; Python computes `added`/`removed`
via the existing `diff_stats()` helper and forwards the event. The JS keeps an
ordered list of rendered items (mirroring `self.items`) so updates are
incremental via morphdom rather than full `setHtml()` blasts.

Slot rewiring in `ChatWindow` (~10 lines change):

| Current slot | Now calls |
|---|---|
| `add_user(text)` | `bridge.push({"type":"user","text":text})` |
| `add_agent(text)` | `bridge.push({"type":"assistant_message","content":text,"streaming":False})` |
| `add_tool(name,args,result)` | `bridge.push({"type":"tool_result",...})` |
| `add_diff(path,diff)` | `bridge.push({"type":"diff",...})` (Python computes added/removed) |
| `add_reasoning(text)` | `bridge.push({"type":"reasoning","content":text,"streaming":False})` |
| `add_error(text)` | `bridge.push({"type":"error","text":text})` |
| `on_finished` | `bridge.push({"type":"done"})` |

Theme sync: `apply_theme()` gains one line —
`bridge.push({"type":"theme","mode": self.resolve_mode()})` — so the web CSS
variables track the native light/dark toggle.

### Streaming approach

`agent_events.py` currently calls `complete(stream_messages=False)`, so the
full message arrives at once. opencode does both true streaming *and* paced
reveal. We replicate that.

Problem: `complete()`'s `on_chunk` is a synchronous callback called *during*
the stream — you cannot `yield` from inside it. So `run_agent_events()` is not
turned into a streaming generator.

Fix — an optional chunk callback parameter (backward compatible):

```python
def run_agent_events(client, model_name, messages, max_agent_steps,
                     approval_mode, intent='', on_assistant_chunk=None):
```

- When `on_assistant_chunk` is `None` (Chainlit, tests, unchanged): behaves
  exactly as today — `complete(stream_messages=False)`, yields one final
  `assistant_message`.
- When provided (Qt bridge): calls
  `complete(stream_messages=True, on_chunk=on_assistant_chunk)`. The callback
  receives the accumulated text so far and pushes incremental events directly
  to the webview:
  ```python
  def on_chunk(accumulated):
      bridge.push({"type":"assistant_message","content":accumulated,"streaming":True})
  ```
- After `complete()` returns, the generator yields the final
  `{"type":"assistant_message","content":full,"streaming":False}` so the JS
  knows the message is complete (stop paced reveal, enable copy buttons,
  finalize tool calls).

`AgentWorker` gains an `on_assistant_chunk` constructor arg wired to the
bridge. Tool calls happen *after* streaming completes, same as now. Reasoning
streams the same way — an `on_reasoning` callback pushes
`{"type":"reasoning","content":accumulated,"streaming":True}` chunks; the
thinking block updates live with a shimmer until `streaming:false`.

Note on the two coexisting paths: the callbacks push incremental
`streaming:True` chunks *during* the stream (direct to the bridge). The
generator's yielded `reasoning`/`assistant_message` events — which
`AgentWorker` turns into `reasoningMessage`/`assistantMessage` signals, handled
by the `add_reasoning`/`add_agent` slots in the table above — become the
`streaming:False` finalizers once the stream completes. When
`on_assistant_chunk` is `None` (Chainlit/tests), the callbacks are not wired
and only the final `streaming:False` events flow, exactly as today.

JS paced reveal (~40 lines, ported from opencode's `PacedMarkdown` /
`createPacedValue`):

- Keeps `shown` (currently revealed text) and a timer.
- On each `assistant_message` event with `streaming:true`: if the new text
  extends `shown` and the delta > 512 chars, reveal in 2-8 char steps at 24ms,
  snapping to whitespace; else reveal immediately.
- `streaming:false` -> reveal the rest instantly, mark block complete.
- Between steps, `morphdom` patches the markdown HTML so partial renders stay
  correct (no flicker).

### Web renderer components (`app.js`)

`app.js` (~350 lines) is a single vanilla-JS module with no framework. It
holds an ordered `items` array (mirroring `self.items`) and an
`__appendEvent(json)` dispatcher that routes to per-type renderers. Each item
owns a root DOM node appended to `#transcript`; updates patch in place via
`morphdom` rather than rebuilding the whole page.

Markdown pipeline (~80 lines, the opencode core):

1. `marked.parse(text)` with `fenced_code`, `tables` extensions -> HTML string.
2. Temp-container parse -> for each `<pre><code>`, call
   `shiki.codeToHtml(code, {lang, theme})` -> replace with highlighted HTML.
3. Wrap each highlighted block in `div.markdown-code` with a copy button
   (`navigator.clipboard.writeText`, "Copied" 2s label swap).
4. `morphdom(container, next)` patches the result into the live DOM; copy
   buttons are preserved across patches (opencode's `onBeforeElUpdated` guard
   skips elements with `data-slot="markdown-copy-button"`).
5. Shiki runs in a Web Worker (`shiki.worker.js`) to avoid blocking the UI on
   large messages, with a sync fallback if the worker fails to load.

Renderers (each returns/updates a DOM node):

| Renderer | Behavior |
|---|---|
| `renderUser` | Right-styled user bubble; escaped text. |
| `renderReasoning` | Collapsible "Thinking" header. While `streaming:true`: shimmer animation on the label + live `morphdom` of the markdown body. Extracts a heading (first `#`/`**bold**`) as a preview. On `streaming:false`: stop shimmer, dim body. |
| `renderAssistantMessage` | Paced reveal -> markdown pipeline. On first chunk: create container. On `streaming:false`: reveal rest, enable copy buttons, drop the preceding reasoning block (matches the current `add_agent` behavior). |
| `renderToolStart` | Card: icon (SVG, from a per-tool map) + `name(args)` subtitle + spinner. State: pending. |
| `renderToolResult` | Update the matching pending card -> completed: checkmark + `resultSummary` one-liner + expandable escaped `<pre>` result (4k cap). Match by name + order. |
| `renderDiff` | Collapsible: header = path + `+added -removed` counts; body = per-line colored `<pre>` (green/red/purple hunk headers), sticky on scroll. |
| `renderError` | Red-bordered card. |
| `renderDone` | Stop all shimmers, finalize. |
| `renderTheme` | Swap `data-theme` attribute -> CSS variables flip light/dark. |

Icon map (small SVG set, ~9 icons): `editor`->code-lines,
`run_command`->terminal, `read_files`->glasses,
`search_codebase`->magnifying-glass, `fetch_web`->globe, `memory`->brain,
`skills`->bookmark, `sessions`->clock, `ask_question`->bubble. Mirrors
opencode's `getToolInfo`.

Sticky accordion for diffs uses `position: sticky; top: 44px` on the header
(opencode's `--sticky-accordion-offset` pattern), pure CSS.

### Theming, fallbacks & testing

Theming — the existing `THEMES` dict (light/dark palettes with `surface`,
`text`, `subtle`, `diff_*` colors) ports to CSS custom properties in
`styles.css`, keyed off `body[data-theme="light|dark"]`. `apply_theme()` pushes
`{"type":"theme","mode": self.resolve_mode()}`; the JS sets
`document.body.dataset.theme` and the variables flip. Shiki gets two themes
bundled (`github-light`, `github-dark`); `app.js` calls `shiki.setTheme` on the
same event so code highlighting tracks the toggle. No separate `themes.json`;
colors live in `styles.css` as one source of truth.

Fallbacks (graceful degradation, matching the existing `md_to_html`
ImportError pattern):

- Shiki fails to load -> code blocks render as plain styled `<pre><code>` (no
  syntax colors). App still works.
- `marked` fails -> escape + `<br>` fallback (same as current `md_to_html`).
- `morphdom` fails -> full `innerHTML` replacement (works, minor flicker on
  streaming).
- `PySide6.QtWebEngineWidgets` not installed -> `ChatWindow` detects the
  `ImportError` and falls back to the current `QTextBrowser` + `render_item()`
  path, so the app never breaks for users without WebEngine. The bridge class
  is only imported when WebEngine is present.

Loading & packaging — `QWebEngineView` loads `web/index.html` via `file://`
(local assets). `app.js`, vendored libs, and `styles.css` are referenced by
relative `<script src>`/`<link>`. For PyInstaller, `SimpleAgentNative.spec`
gains `datas=[('web','web')]` so assets ship inside the bundle.

Testing — the project uses `unittest` with no JS test harness. Split:

- Unit-testable in Python (new `test_qt_bridge_behavior.py`): the bridge's
  event serialization (signal -> correct JSON), theme-mode mapping,
  `diff_stats` -> JSON. These do not need a running view — test the pure
  functions.
- The web rendering (`app.js`): verified manually via the established workflow
  in `CLAUDE.md` (compile with real Windows Python, launch via Run dialog). A
  visual checklist: streaming markdown, highlighted code, tool cards
  pending->completed, reasoning shimmer, inline diff colors, copy button,
  light/dark toggle. No JS unit tests — out of scope for this project's stack.
- Existing tests stay green: `run_agent_events()` only gains an optional param
  (default `None`), so `test_events.py` and any `agent_events` tests are
  unaffected.

## Verification

- `uv run python -m py_compile qt_app.py` is clean (real Windows Python).
- `uv run python -m pytest test_qt_bridge_behavior.py` passes.
- Existing test files (`test_main_behavior.py`, `test_tools_behavior.py`,
  `test_safety_behavior.py`) still pass.
- Manual launch via Run dialog with the venv on `PYTHONPATH`: send a test
  message, confirm each item on the visual checklist renders correctly, toggle
  light/dark, confirm code highlighting tracks.
