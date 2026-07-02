# SimpleAgent — Project Memory (CLAUDE.md)

A Python coding agent. An LLM plans and calls tools in a loop to read, search, edit,
and run commands inside whatever project folder it is launched in. Multiple frontends
share one core.

- **Model / provider:** `deepseek/deepseek-v4-flash` via an OpenAI-compatible endpoint
  (config default points at OpenRouter; the working setup uses the **opencode-go**
  provider). API key comes from `.env` as `OPENCODE_GO_API_KEY`. Never print/expose it.
- **Two homes:**
  - `AGENT_HOME` — where this code lives; holds `.env` and `agent_config.json`.
  - Workspace = the folder the agent is launched in; per-project state lives in its
    `.simpleagent/` (sessions, logs, memory, skills, exports).

## Layout

- `agent.py` — `run_tool()` (looks name up in `AVAILABLE_TOOL`, parses JSON args,
  calls the function), tool-call helpers, assistant-message plumbing.
- `agent_events.py` — `run_agent_events()` generator: the step loop. Calls `complete()`,
  yields typed events (`reasoning`, `assistant_message`, `tool_start`, `tool_result`,
  `approval_request`, `question_request`, `done`). Frontends consume these.
- `llm.py` — `complete()` builds the request (`model`, `messages`, and when tools are
  passed, `tools` + `tool_choice="auto"`), streaming and non-streaming paths, retry with
  backoff. Reads `reasoning_content` from deltas/`model_extra`.
- `config.py` — `DEFAULT_CONFIG`, `load_config`/`save_config`, token/context helpers,
  and the tool wiring: `TOOLS` (schemas the LLM sees) + `AVAILABLE_TOOL` (name → function).
- `tools.py` — all tool implementations plus the `@tool` decorator and the 9 public
  dispatchers (see below). `safety.py` — `decide_command()` gates shell commands.
- `tools_schema.json` — the tool schemas sent to the model. Must stay in sync with
  `AVAILABLE_TOOL`.
- `prompt.py` — system prompt. `sessions.py` — save/load/list/rename/delete/export
  session JSON (`list_saved_sessions` sorts newest-first by `updated_at`).
- Frontends: `main.py` (CLI, terminal REPL; `ui.py` holds its printing/formatting
  helpers) and **`qt_app.py`** (the main PySide6 desktop GUI — see below). The
  Chainlit browser UI (`chainlit_app.py`) and its `desktop.py` pywebview wrapper
  were removed — CLI + native Qt only now.

## Tool structure (RECENTLY CHANGED — how the LLM sees tools)

The old ~28 flat tools were **consolidated into 9 multiplexed tools**. Each groups
related operations behind a single tool name plus a selector parameter, so the model
sees a smaller, cleaner tool list. The granular functions (`write_file`,
`read_file_range`, `web_search`, etc.) still exist inside `tools.py` as internal helpers;
the 9 public tools dispatch to them. Registration is via the `@tool` decorator
(wraps a function so any exception becomes an `"ERROR: ..."` string instead of crashing).

`AVAILABLE_TOOL` and `tools_schema.json` both expose exactly these 9:

| Tool | Selector param | Sub-operations |
|------|----------------|----------------|
| `read_files` | `mode` | `read` (default), `list`, `tree`, `glob`, `info`. Also `start_line`/`end_line`, multi-path reads. Replaces the old read/list/tree/glob/info tools. |
| `search_codebase` | — | regex/word search; `path`, `file_glob`. |
| `editor` | `operation` | `write`, `patch` (unique `old_text`→`new_text` replacements), `delete`, `move`. Replaces write_file/apply_patch/delete_file/move_file. |
| `run_command` | — | one shell command; gated by `safety.decide_command`. Renamed from `execute_terminal_command`. File-inspection commands are blocked here (use `read_files`). |
| `fetch_web` | url vs query | pass `url` to fetch a page (optional `prompt` to distill), or `query` to web-search (Tavily). |
| `memory` | `scope` | `global` (about the user, ≤2000 chars) or `project` (≤6000). Full overwrite. |
| `skills` | `action` | `list`, `read`, `save`, `delete` reusable markdown runbooks. |
| `sessions` | `action` | `search`, `read` past conversations in this project. |
| `ask_question` | — | ask the user a clarifying question with 2–5 selectable options. |

`run_tool` special-cases `run_command` to also pass `approval_mode`. In
`agent_events`, `run_command` goes through `decide_command` (may yield
`approval_request`), and `ask_question` yields `question_request` and feeds the answer
back as `f"User chose: {answer}"`.

To add/change a tool: edit the dispatcher in `tools.py`, update its schema in
`tools_schema.json`, and add it to `AVAILABLE_TOOL` in `config.py`. Keep all three aligned.

## Reasoning support

- `config.py`: `DEFAULT_CONFIG["reasoning"] = "default"`.
- `llm.py`: `set_reasoning_effort(level)` stores `"low"/"medium"/"high"` (anything else →
  `None`); `complete()` adds `request["reasoning_effort"]` only when a level is set, so
  providers that don't support it are unaffected. Reasoning text streams via
  `reasoning_content` and surfaces as `reasoning` events.
- Accepted by opencode-go; behavioral effect on deepseek-v4-flash is unconfirmed.

## qt_app.py (PySide6 desktop GUI)

Frameless custom-chrome window (intentional). Structure: helper funcs → `AgentWorker(QThread)`
→ `SkillsToolsDialog` → `SettingsDialog` → `TitleBar` → `SessionRow` → `ChatWindow`.

- **Body:** `QSplitter(Horizontal)` of `[left_panel, chat_panel, right_panel]` —
  push panels, independently openable, draggable divider (`handleWidth=6`,
  `childrenCollapsible=False`); chat has a ~1/3 min width. One `QSizeGrip`; status-bar grip off.
- **Left panel:** `＋ Project folder`, `＋ New session`, then all-caps `▪ PINNED SESSIONS` /
  `▪ PAST SESSIONS`; rows are `SessionRow` (`• dot`, name, relative-time label e.g. "2d",
  per-row `⋮` menu: Pin/Unpin, Copy ID, Export, Rename, Delete). `NoFocus`/`NoSelection`
  to kill focus-rect artifacts. Sessions only re-save when a `_dirty` flag is set (on send),
  so leaving a session no longer bumps its order.
- **Right panel:** `QStackedWidget` — page 0 file tree, page 1 `[← Files][editor_tabs]`
  (a file covers the whole panel).
- **Worker events:** `reasoning`→collapsible "▶ Thinking"; `assistant_message`;
  `tool_start`/`tool_result` with inline diffs; `question_request`→`on_question` dialog.
- **Settings:** model, approval mode, max steps, theme, **Reasoning** dropdown, and a
  **Skills & Tools…** button opening `SkillsToolsDialog` (renders `config.TOOLS` +
  `skills` list). Escape `&` as `&&` in Qt button text.

## Inline-diff / editor-tool wiring (was broken by the consolidation, now fixed)

The tool consolidation renamed file edits from four tools to one (`editor` + `operation`).
`qt_app.py`'s inline-diff feature was keyed on the old names and stopped rendering diffs.
Fixed: `FILE_EDIT_TOOLS = ("editor",)` and `diff_targets()` now matches `name == "editor"`,
reads the `operation` arg, and pulls `path` (write/patch/delete) or `source`/`destination`
(move). If a new file-editing operation is added to the `editor` tool, update `diff_targets`
too or its diff won't show.

## Verification workflow

The Linux sandbox mount can serve stale/truncated copies. Compile with the **real Windows
Python** (`...python.exe -m py_compile qt_app.py 2> _compile.txt`; empty file = clean) and
launch via the Run dialog with the venv on `PYTHONPATH`. Do **not** hard-delete files in the
user's project — flag throwaway files (`_compile.txt`, `_qt_err*.log`) for the user to remove.
