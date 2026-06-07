# Agent Broader Pass Design

Date: 2026-06-07

## Goal

Improve AgentSimple's reliability, installed behavior, and day-to-day smoothness without turning the recent refactor into a large rewrite.

The pass should keep the current clean spine:

- `main.py` owns startup and the command loop.
- `agent.py` owns tool-call orchestration and compaction.
- `llm.py` owns OpenAI-compatible chat completion handling.
- `prompt.py` owns system prompt, memory, and skill context assembly.
- `sessions.py` owns saved conversation files.
- `ui.py` owns terminal display and slash-command UI.
- `tools.py` remains the tool surface for now, with only small local cleanup unless a tiny extraction is clearly useful.

## Scope

Implement the following improvements:

1. Fix build packaging so installed wheels include all runtime modules and `tools_schema.json`.
2. Add persistent config saving so `/mode` updates `agent_config.json` and survives restart.
3. Sort session search results by session `updated_at`, newest first, rather than relying on filename order.
4. Clarify and harden `web_fetch` around HTTP-to-HTTPS upgrade behavior and local/private address blocking.
5. Add a simple context-health nudge that warns when approximate context usage is high and suggests `/compact`.
6. Add focused tests for each behavior.

Out of scope:

- A full `tools.py` module split.
- New external dependencies.
- New model-provider abstractions.
- Major changes to the agent prompt personality or tool list.
- Automatic compaction that rewrites the conversation without user confirmation.

## Design

### Packaging

Update `pyproject.toml` so the wheel includes every runtime file introduced by the refactor:

- `main.py`
- `agent.py`
- `config.py`
- `llm.py`
- `prompt.py`
- `sessions.py`
- `ui.py`
- `tools.py`
- `safety.py`
- `tools_schema.json`

This protects `simpleagent` after `uv tool install` or wheel builds.

### Config Persistence

Add `save_config(config: dict) -> None` in `config.py`.

`save_config` should merge with defaults, write stable pretty JSON to `agent_config.json`, and preserve unknown user keys if they are already present in the in-memory config. `ui.choose_mode` should call it after changing `approval_mode`.

If saving fails, `choose_mode` should report that the mode changed only for the current run and show the error. It should not crash the chat loop.

### Session Search Ordering

Change `tools.search_sessions` to read candidate session metadata first, then sort by `updated_at` descending before scanning content. Sessions with invalid or missing timestamps should fall back to the filename or an empty string so corrupt metadata does not crash search.

### Web Fetch Hardening

Keep the current behavior of upgrading `http://` URLs to `https://`. Update the error text to say HTTPS is required after upgrade, not that both schemes are allowed.

Normalize hostnames to lowercase before private-host checks. Keep the existing prefix-based block list for now, but make the message clearer.

Do not add DNS resolution or network probing in this pass.

### Context Health

Add a small helper that calculates context usage from `estimate_message_tokens(messages)` and `context_window_tokens`.

When usage crosses a conservative threshold, show a concise warning in `/status` and after saved turns. The warning should suggest `/compact`, but should not force compaction or make an extra model call.

Default threshold: 70 percent of configured context.

### Tests

Add or update tests for:

- wheel include list contains refactored runtime files and `tools_schema.json`
- `save_config` writes merged JSON and `/mode` calls it
- `search_sessions` returns matches newest-first by `updated_at`
- `web_fetch` rejects non-HTTPS schemes with accurate wording and normalizes host checks
- context-health helper warns over threshold and stays quiet below threshold

Run:

- `.venv\Scripts\python.exe -m unittest`
- `.venv\Scripts\python.exe -m py_compile main.py agent.py config.py llm.py prompt.py sessions.py tools.py safety.py ui.py`

## Risks

The main risk is touching user-facing terminal behavior in a way that adds noise. Keep warnings short and only show them at meaningful thresholds.

The second risk is packaging configuration. Verify by checking the wheel include list in tests; if local build tooling is available, also run a wheel build or equivalent smoke check.

## Success Criteria

- Installed package has all runtime files required by the refactor.
- `/mode` persists after restart.
- Session search ordering matches actual update time.
- Web fetch messages are accurate and private-host checks are case-insensitive.
- The agent gently warns about high context use without extra model calls.
- Full tests and compile checks pass.
