# Agent Broader Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden AgentSimple's installed behavior, config persistence, session recall, web fetch safety, and context-health feedback while keeping the recent refactor compact.

**Architecture:** Keep the existing refactor spine. Add small helpers where behavior naturally belongs: packaging in `pyproject.toml`, config writing and context math in `config.py`, terminal warning display in `ui.py`, turn-level warning call in `main.py`, and tool-specific fixes in `tools.py`.

**Tech Stack:** Python 3.13, unittest, Rich, OpenAI-compatible client, hatchling build config.

---

## File Structure

- Modify `pyproject.toml`: include all runtime modules and `tools_schema.json` in the wheel.
- Modify `config.py`: add `save_config`, context warning defaults, and context-health helper functions.
- Modify `ui.py`: persist `/mode` changes and display context warnings in `/status`.
- Modify `main.py`: show context warning after successful turn saves.
- Modify `tools.py`: sort session search by `updated_at` and improve `web_fetch` scheme and host checks.
- Modify `test_main_behavior.py`: tests for packaging, config persistence, `/mode`, and context warnings.
- Modify `test_tools_behavior.py`: tests for session search ordering and `web_fetch` hardening.

Execution note: this worktree already contains uncommitted refactor files. Before each commit step, check `git status --short`. Commit only if the staged diff contains this task's intended changes and does not sweep unrelated user/other-agent changes into the commit.

---

### Task 1: Fix Wheel Include List

**Files:**
- Modify: `pyproject.toml`
- Modify: `test_main_behavior.py`

- [ ] **Step 1: Write the failing packaging test**

Add this test method to `MainBehaviorTests` in `test_main_behavior.py`:

```python
    def test_wheel_include_list_contains_runtime_files(self):
        from pathlib import Path

        text = Path("pyproject.toml").read_text(encoding="utf-8")
        required_files = [
            "main.py",
            "agent.py",
            "config.py",
            "llm.py",
            "prompt.py",
            "sessions.py",
            "ui.py",
            "tools.py",
            "safety.py",
            "tools_schema.json",
        ]

        for file_name in required_files:
            self.assertIn(file_name, text)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_wheel_include_list_contains_runtime_files
```

Expected result before implementation:

```text
FAIL
AssertionError: 'agent.py' not found
```

- [ ] **Step 3: Update `pyproject.toml` include list**

Replace the wheel include list with:

```toml
include = [
    "main.py",
    "agent.py",
    "config.py",
    "llm.py",
    "prompt.py",
    "sessions.py",
    "ui.py",
    "tools.py",
    "safety.py",
    "tools_schema.json",
]
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_wheel_include_list_contains_runtime_files
```

Expected result:

```text
Ran 1 test
OK
```

- [ ] **Step 5: Commit if staging is isolated**

Run:

```powershell
git status --short
git diff -- pyproject.toml test_main_behavior.py
git add pyproject.toml test_main_behavior.py
git diff --cached -- pyproject.toml test_main_behavior.py
```

If the cached diff contains only this task's packaging test and include-list change, run:

```powershell
git commit -m "fix: include refactored runtime files in wheel"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the task uncommitted:

```powershell
git restore --staged pyproject.toml test_main_behavior.py
```

---

### Task 2: Persist `/mode` Config Changes

**Files:**
- Modify: `config.py`
- Modify: `ui.py`
- Modify: `test_main_behavior.py`

- [ ] **Step 1: Write failing tests for `save_config` and `/mode`**

Add these test methods to `MainBehaviorTests` in `test_main_behavior.py`:

```python
    def test_save_config_writes_merged_json(self):
        from pathlib import Path

        config_path = Path("tmp_agent_config_save.json")
        self.addCleanup(lambda: config_path.exists() and config_path.unlink())

        with patch.object(config, "CONFIG_PATH", config_path):
            config.save_config(
                {
                    "approval_mode": "full_auto",
                    "custom_setting": "kept",
                }
            )

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["approval_mode"], "full_auto")
        self.assertEqual(saved["custom_setting"], "kept")
        self.assertEqual(saved["model"], config.DEFAULT_CONFIG["model"])

    def test_choose_mode_persists_selected_mode(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["approval_mode"] = "ask"

        with patch("builtins.input", return_value="2"), \
                patch("ui.save_config") as fake_save:
            with redirect_stdout(io.StringIO()):
                ui.choose_mode(runtime_config)

        self.assertEqual(runtime_config["approval_mode"], "safe_auto")
        fake_save.assert_called_once_with(runtime_config)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_save_config_writes_merged_json test_main_behavior.MainBehaviorTests.test_choose_mode_persists_selected_mode
```

Expected result before implementation:

```text
ERROR
AttributeError: module 'config' has no attribute 'save_config'
```

- [ ] **Step 3: Add `save_config` to `config.py`**

Add this function after `load_config` in `config.py`:

```python
def save_config(config: dict) -> None:
    final_config = DEFAULT_CONFIG.copy()
    final_config.update(config)

    CONFIG_PATH.write_text(
        json.dumps(final_config, indent=4) + "\n",
        encoding="utf-8",
    )
```

- [ ] **Step 4: Wire `save_config` into `ui.py`**

Update the `config` import in `ui.py` to include `save_config`:

```python
from config import (
    AVAILABLE_TOOL,
    TOOLS,
    estimate_message_tokens,
    get_context_window_tokens,
    get_tool_display,
    save_config,
)
```

Replace the final mode-change print block in `choose_mode` with:

```python
    selected_mode = modes[int(choice) - 1]
    config["approval_mode"] = selected_mode

    try:
        save_config(config)
    except Exception as error:
        print()
        print(f"Mode changed for this run only: {selected_mode}")
        print(f"Could not save agent_config.json: {error}")
        print()
        return

    print()
    print(f"Mode changed to: {selected_mode}")
    print()
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_save_config_writes_merged_json test_main_behavior.MainBehaviorTests.test_choose_mode_persists_selected_mode
```

Expected result:

```text
Ran 2 tests
OK
```

- [ ] **Step 6: Commit if staging is isolated**

Run:

```powershell
git status --short
git diff -- config.py ui.py test_main_behavior.py
git add config.py ui.py test_main_behavior.py
git diff --cached -- config.py ui.py test_main_behavior.py
```

If the cached diff contains only this task's config persistence changes, run:

```powershell
git commit -m "feat: persist approval mode changes"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the task uncommitted:

```powershell
git restore --staged config.py ui.py test_main_behavior.py
```

---

### Task 3: Add Context-Health Nudge

**Files:**
- Modify: `config.py`
- Modify: `ui.py`
- Modify: `main.py`
- Modify: `test_main_behavior.py`

- [ ] **Step 1: Write failing tests for context-health behavior**

Add these test methods to `MainBehaviorTests` in `test_main_behavior.py`:

```python
    def test_context_health_warning_triggers_over_threshold(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 320},
        ]
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 100
        runtime_config["context_warning_percent"] = 70

        warning = config.context_health_warning(messages, runtime_config)

        self.assertIn("Context is about", warning)
        self.assertIn("/compact", warning)

    def test_context_health_warning_stays_quiet_below_threshold(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "short"},
        ]
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 1000
        runtime_config["context_warning_percent"] = 70

        self.assertEqual(config.context_health_warning(messages, runtime_config), "")

    def test_show_context_warning_prints_only_when_needed(self):
        runtime_config = config.DEFAULT_CONFIG.copy()
        runtime_config["context_window_tokens"] = 100
        runtime_config["context_warning_percent"] = 70
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 320},
        ]

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ui.show_context_warning(messages, runtime_config)

        output = buffer.getvalue()
        self.assertIn("Context is about", output)
        self.assertIn("/compact", output)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_context_health_warning_triggers_over_threshold test_main_behavior.MainBehaviorTests.test_context_health_warning_stays_quiet_below_threshold test_main_behavior.MainBehaviorTests.test_show_context_warning_prints_only_when_needed
```

Expected result before implementation:

```text
ERROR
AttributeError: module 'config' has no attribute 'context_health_warning'
```

- [ ] **Step 3: Add context warning default and helpers to `config.py`**

Add this key to `DEFAULT_CONFIG`:

```python
    "context_warning_percent": 70,
```

Add these functions after `get_context_window_tokens` in `config.py`:

```python
def get_context_usage_percent(messages: list[dict], config: dict) -> float:
    context_window = max(1, get_context_window_tokens(config))
    estimated_tokens = estimate_message_tokens(messages)
    return estimated_tokens / context_window * 100


def get_context_warning_percent(config: dict) -> float:
    try:
        return float(config.get("context_warning_percent", 70))
    except (TypeError, ValueError):
        return 70.0


def context_health_warning(messages: list[dict], config: dict) -> str:
    percent = get_context_usage_percent(messages, config)
    threshold = get_context_warning_percent(config)

    if percent < threshold:
        return ""

    return (
        f"Context is about {percent:.1f}% full. "
        "Consider /compact before starting a large task."
    )
```

- [ ] **Step 4: Add warning display helper to `ui.py`**

Update the `config` import in `ui.py` to include `context_health_warning`:

```python
from config import (
    AVAILABLE_TOOL,
    TOOLS,
    context_health_warning,
    estimate_message_tokens,
    get_context_window_tokens,
    get_tool_display,
    save_config,
)
```

Add this helper near `show_status` in `ui.py`:

```python
def show_context_warning(messages: list[dict], config: dict) -> None:
    warning = context_health_warning(messages, config)

    if not warning:
        return

    print()
    print(warning)
    print()
```

Add this to the end of `show_status`, before the final blank `print()`:

```python
    warning = context_health_warning(messages, config)
    if warning:
        print(f"Context warning: {warning}")
```

- [ ] **Step 5: Show the warning after successful saved turns in `main.py`**

Update the `ui` import in `main.py` to include `show_context_warning`:

```python
from ui import (
    choose_mode,
    clear_conversation,
    is_skill_question,
    print_welcome,
    rewind_conversation,
    show_context_warning,
    show_help,
    show_memory,
    show_skills,
    show_status,
    show_tools,
)
```

After this line in the successful turn block:

```python
            save_session(current_session_name, model_name, messages)
```

add:

```python
            show_context_warning(messages, config)
```

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main_behavior.MainBehaviorTests.test_context_health_warning_triggers_over_threshold test_main_behavior.MainBehaviorTests.test_context_health_warning_stays_quiet_below_threshold test_main_behavior.MainBehaviorTests.test_show_context_warning_prints_only_when_needed
```

Expected result:

```text
Ran 3 tests
OK
```

- [ ] **Step 7: Commit if staging is isolated**

Run:

```powershell
git status --short
git diff -- config.py ui.py main.py test_main_behavior.py
git add config.py ui.py main.py test_main_behavior.py
git diff --cached -- config.py ui.py main.py test_main_behavior.py
```

If the cached diff contains only this task's context-health changes, run:

```powershell
git commit -m "feat: warn when context usage is high"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the task uncommitted:

```powershell
git restore --staged config.py ui.py main.py test_main_behavior.py
```

---

### Task 4: Sort Session Search by `updated_at`

**Files:**
- Modify: `tools.py`
- Modify: `test_tools_behavior.py`

- [ ] **Step 1: Write the failing session ordering test**

Add this test method to `ToolsBehaviorTests` in `test_tools_behavior.py`:

```python
    def test_search_sessions_orders_matches_by_updated_at(self):
        sessions_dir = Path("tmp_sessions_search_order")
        self.addCleanup(lambda: shutil.rmtree(sessions_dir, ignore_errors=True))
        sessions_dir.mkdir(exist_ok=True)

        older_file = sessions_dir / "session-z.json"
        newer_file = sessions_dir / "session-a.json"

        older_file.write_text(
            json.dumps(
                {
                    "name": "session-z",
                    "updated_at": "2026-01-01T10:00:00",
                    "messages": [
                        {"role": "user", "content": "needle from older session"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        newer_file.write_text(
            json.dumps(
                {
                    "name": "session-a",
                    "updated_at": "2026-01-02T10:00:00",
                    "messages": [
                        {"role": "user", "content": "needle from newer session"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with patch.object(tools, "SESSIONS_DIR", sessions_dir):
            result = tools.search_sessions("needle")

        self.assertLess(result.index("session-a"), result.index("session-z"))
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_tools_behavior.ToolsBehaviorTests.test_search_sessions_orders_matches_by_updated_at
```

Expected result before implementation:

```text
FAIL
AssertionError
```

- [ ] **Step 3: Update `tools.search_sessions` to sort loaded records**

Replace this line in `tools.search_sessions`:

```python
    session_files = sorted(SESSIONS_DIR.glob("*.json"), reverse=True)
```

and the following `for session_file in session_files:` loop setup with:

```python
    session_records = []

    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        sort_key = str(data.get("updated_at") or session_file.stem)
        session_records.append((sort_key, session_file, data))

    session_records.sort(key=lambda record: record[0], reverse=True)

    for _sort_key, session_file, data in session_records:
```

Inside the loop, remove the now-duplicate block:

```python
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            continue
```

Keep the existing `name`, `updated_at`, message scanning, match limit, and output formatting logic unchanged.

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_tools_behavior.ToolsBehaviorTests.test_search_sessions_orders_matches_by_updated_at
```

Expected result:

```text
Ran 1 test
OK
```

- [ ] **Step 5: Commit if staging is isolated**

Run:

```powershell
git status --short
git diff -- tools.py test_tools_behavior.py
git add tools.py test_tools_behavior.py
git diff --cached -- tools.py test_tools_behavior.py
```

If the cached diff contains only this task's session ordering change, run:

```powershell
git commit -m "fix: sort session search by update time"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the task uncommitted:

```powershell
git restore --staged tools.py test_tools_behavior.py
```

---

### Task 5: Harden `web_fetch` Scheme and Host Checks

**Files:**
- Modify: `tools.py`
- Modify: `test_tools_behavior.py`

- [ ] **Step 1: Write failing tests for `web_fetch` hardening**

Add these helper classes near the top of `test_tools_behavior.py`, after imports:

```python
class FakeHeaders:
    def get_content_type(self):
        return "text/plain"

    def get_content_charset(self):
        return "utf-8"


class FakeWebResponse:
    headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, max_bytes):
        return b"hello from web"
```

Add these test methods to `ToolsBehaviorTests` in `test_tools_behavior.py`:

```python
    def test_web_fetch_rejects_non_https_scheme_with_clear_message(self):
        result = tools.web_fetch("ftp://example.com/data")

        self.assertIn("ERROR", result)
        self.assertIn("HTTPS URLs are required", result)

    def test_web_fetch_blocks_localhost_case_insensitively(self):
        result = tools.web_fetch("https://LOCALHOST/status")

        self.assertIn("ERROR", result)
        self.assertIn("local or private", result)

    def test_web_fetch_upgrades_http_to_https_before_request(self):
        seen = {}
        tools.WEB_FETCH_CACHE.clear()

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return FakeWebResponse()

        with patch("tools.urllib.request.urlopen", side_effect=fake_urlopen):
            result = tools.web_fetch("http://example.com/data")

        self.assertEqual(seen["url"], "https://example.com/data")
        self.assertIn("hello from web", result)
```

- [ ] **Step 2: Run the focused tests and verify at least one fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_tools_behavior.ToolsBehaviorTests.test_web_fetch_rejects_non_https_scheme_with_clear_message test_tools_behavior.ToolsBehaviorTests.test_web_fetch_blocks_localhost_case_insensitively test_tools_behavior.ToolsBehaviorTests.test_web_fetch_upgrades_http_to_https_before_request
```

Expected result before implementation:

```text
FAIL
AssertionError: 'HTTPS URLs are required' not found
```

- [ ] **Step 3: Update scheme and host handling in `tools.web_fetch`**

Replace this block:

```python
    if parsed.scheme != "https":
        return "ERROR: Only http and https URLs are allowed."

    host = parsed.hostname or ""

    if any(host.startswith(prefix) for prefix in BLOCKED_FETCH_HOSTS):
        return "ERROR: Fetching local or private addresses is not allowed."
```

with:

```python
    if parsed.scheme != "https":
        return "ERROR: HTTPS URLs are required."

    host = (parsed.hostname or "").lower()

    if any(host.startswith(prefix) for prefix in BLOCKED_FETCH_HOSTS):
        return "ERROR: Fetching local or private network addresses is not allowed."
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_tools_behavior.ToolsBehaviorTests.test_web_fetch_rejects_non_https_scheme_with_clear_message test_tools_behavior.ToolsBehaviorTests.test_web_fetch_blocks_localhost_case_insensitively test_tools_behavior.ToolsBehaviorTests.test_web_fetch_upgrades_http_to_https_before_request
```

Expected result:

```text
Ran 3 tests
OK
```

- [ ] **Step 5: Commit if staging is isolated**

Run:

```powershell
git status --short
git diff -- tools.py test_tools_behavior.py
git add tools.py test_tools_behavior.py
git diff --cached -- tools.py test_tools_behavior.py
```

If the cached diff contains only this task's `web_fetch` changes, run:

```powershell
git commit -m "fix: clarify web fetch URL safety"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the task uncommitted:

```powershell
git restore --staged tools.py test_tools_behavior.py
```

---

### Task 6: Final Verification

**Files:**
- Inspect: `pyproject.toml`
- Inspect: `main.py`
- Inspect: `config.py`
- Inspect: `ui.py`
- Inspect: `tools.py`
- Inspect: `test_main_behavior.py`
- Inspect: `test_tools_behavior.py`

- [ ] **Step 1: Run the full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest
```

Expected result:

```text
OK
```

- [ ] **Step 2: Run Python compile verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile main.py agent.py config.py llm.py prompt.py sessions.py tools.py safety.py ui.py
```

Expected result:

```text
<no output, exit code 0>
```

- [ ] **Step 3: Check package metadata manually**

Run:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; text=Path('pyproject.toml').read_text(encoding='utf-8'); required=['main.py','agent.py','config.py','llm.py','prompt.py','sessions.py','ui.py','tools.py','safety.py','tools_schema.json']; missing=[item for item in required if item not in text]; print('missing=' + repr(missing))"
```

Expected result:

```text
missing=[]
```

- [ ] **Step 4: Run an optional wheel build when local tooling is available**

Run:

```powershell
uv build --wheel
```

Expected result when build dependencies are already available:

```text
Successfully built dist\agentsimple-0.2.0-py3-none-any.whl
```

If this fails because the sandbox cannot download or resolve build dependencies, record the failure and rely on the passing include-list test.

- [ ] **Step 5: Inspect final diff**

Run:

```powershell
git status --short
git diff -- pyproject.toml main.py config.py ui.py tools.py test_main_behavior.py test_tools_behavior.py
```

Expected result:

```text
Only the approved broader-pass changes appear in these files.
```

- [ ] **Step 6: Commit final changes if staging is isolated**

Run:

```powershell
git status --short
git add pyproject.toml main.py config.py ui.py tools.py test_main_behavior.py test_tools_behavior.py
git diff --cached -- pyproject.toml main.py config.py ui.py tools.py test_main_behavior.py test_tools_behavior.py
```

If previous task commits were skipped because of pre-existing dirty files, commit only when the cached diff is limited to this broader pass:

```powershell
git commit -m "feat: harden agent runtime polish"
```

If the cached diff includes unrelated pre-existing changes, unstage and leave the final changes uncommitted:

```powershell
git restore --staged pyproject.toml main.py config.py ui.py tools.py test_main_behavior.py test_tools_behavior.py
```

---

## Self-Review Notes

- Spec coverage: Task 1 covers packaging; Task 2 covers config persistence; Task 3 covers context-health nudge; Task 4 covers session search ordering; Task 5 covers `web_fetch` hardening; Task 6 covers verification.
- Scope control: The plan does not split `tools.py`, add dependencies, add provider abstractions, change prompt personality, or add automatic compaction.
- Type consistency: `save_config(config: dict) -> None`, `context_health_warning(messages: list[dict], config: dict) -> str`, and `show_context_warning(messages: list[dict], config: dict) -> None` are used consistently across tests and implementation steps.
