"""A native desktop frontend for the agent, built with PySide6 (Qt).

No web server, no HTML page: this process calls run_agent_events() directly and
renders the events with native OS widgets. The agent core is unchanged.
"""

import difflib
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv
from openai import OpenAI
from PySide6.QtCore import (
    Qt,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRegularExpression,
    QSettings,
    QSize,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QHBoxLayout,
    QAbstractItemView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from config import AGENT_HOME, TOOLS, load_config, save_config
from prompt import current_system_message, apply_system_message
from tools import set_llm, set_current_intent, list_skills
from agent_events import run_agent_events
from llm import set_reasoning_effort
from sessions import (
    create_session_name,
    delete_session_file,
    list_saved_sessions,
    load_session,
    rename_session,
    save_session,
    format_relative_time,
)

# Folder of static web assets (sibling to this file).
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_ASSETS = os.path.join(WEB_DIR, "web")

# --- Markdown -> HTML, with a graceful fallback if the lib isn't installed ---
try:
    import markdown as _markdown

    def md_to_html(text):
        return _markdown.markdown(
            text or "", extensions=["fenced_code", "tables", "sane_lists"]
        )

except ImportError:

    def md_to_html(text):
        return f"<span>{escape(text)}</span>"


def escape(text, br=True):
    s = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if br:
        s = s.replace("\n", "<br>")
    return s


DETAIL_KEYS = (
    "path", "paths", "source", "command", "pattern",
    "query", "name", "test_path", "target_path",
)


def short_detail(args_text):
    """Pull a human-friendly 'target' out of a tool call's JSON arguments."""
    try:
        args = json.loads(args_text)
    except Exception:
        return ""
    value = next((args[k] for k in DETAIL_KEYS if args.get(k)), "")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return str(value)


def result_summary(result):
    """A short, human one-liner describing a tool's result (like Claude Code's '45 lines')."""
    text = (result or "").strip()
    if not text:
        return "done"
    for prefix in ("SUCCESS", "ERROR", "BLOCKED", "CANCELLED"):
        if text.upper().startswith(prefix):
            rest = " ".join(text[len(prefix):].lstrip(": \n").split())
            return prefix.lower() + (f" - {rest[:48]}" if rest else "")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return f"{len(lines)} lines"
    return " ".join(text.split())[:60]


# --- activity status (aggregate, spinner-driven — no per-call cards) ----------
# A single line per turn accumulates tool-call counts by category and shows a
# spinner while work is in progress, instead of one box per tool call (which
# used to pile up into repeated rows of the same tool name and needed a click
# to see the file text). Mirrors web/app.js's classifyTool/ACTIVITY.

SPINNER_FRAMES = ("◐", "◓", "◑", "◒")

ACTIVITY = {
    "read":     {"progress": "Reading files",        "past": "Read",     "noun": "file"},
    "edited":   {"progress": "Editing files",         "past": "Edited",   "noun": "file"},
    "searched": {"progress": "Searching code",        "past": "Searched", "noun": "search"},
    "ran":      {"progress": "Running a command",     "past": "Ran",      "noun": "command"},
    "fetched":  {"progress": "Fetching the web",      "past": "Fetched",  "noun": "page"},
    "memory":   {"progress": "Updating memory",       "past": "Updated",  "noun": "memory note"},
    "skills":   {"progress": "Managing skills",       "past": "Updated",  "noun": "skill"},
    "sessions": {"progress": "Checking past sessions", "past": "Checked", "noun": "session"},
    "asked":    {"progress": "Asking a question",     "past": "Asked",    "noun": "question"},
    "other":    {"progress": "Working",               "past": "Used",     "noun": "tool"},
}
ACTIVITY_ORDER = (
    "read", "edited", "searched", "ran", "fetched",
    "memory", "skills", "sessions", "asked", "other",
)


def classify_tool_call(name, args_text):
    """Map a tool call to (category, count) for the aggregate status line."""
    try:
        args = json.loads(args_text) if args_text else {}
    except (json.JSONDecodeError, TypeError):
        args = {}

    if name == "read_files":
        paths = args.get("paths")
        count = len(paths) if isinstance(paths, list) else 1
        return "read", max(count, 1)

    if name == "editor":
        operation = (args.get("operation") or "").lower()
        return "edited", (2 if operation == "move" else 1)

    if name == "search_codebase":
        return "searched", 1
    if name == "run_command":
        return "ran", 1
    if name == "fetch_web":
        return "fetched", 1
    if name == "memory":
        return "memory", 1
    if name == "skills":
        return "skills", 1
    if name == "sessions":
        return "sessions", 1
    if name == "ask_question":
        return "asked", 1

    return "other", 1


def format_activity_counts(counts):
    """Render {'read': 3, 'edited': 5, ...} as 'Read 3 files · Edited 5 files'."""
    parts = []
    for key in ACTIVITY_ORDER:
        n = counts.get(key)
        if not n:
            continue
        info = ACTIVITY[key]
        noun = info["noun"] if n == 1 else info["noun"] + "s"
        parts.append(f"{info['past']} {n} {noun}")
    return " · ".join(parts)


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


try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtCore import QUrl

    _HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = None
    _HAS_WEBENGINE = False


if _HAS_WEBENGINE:

    class _BridgePage(QWebEnginePage):
        """A QWebEnginePage that intercepts a custom 'copy:<percent-encoded
        text>' pseudo-URL as a lightweight JS -> Python channel.

        Both the async Clipboard API (navigator.clipboard) and the legacy
        document.execCommand('copy') are unreliable from the file:// origin
        QtWebEngine loads the chat transcript from, so the "Copy" button on
        code blocks instead navigates to this pseudo-URL, which we intercept
        here and copy via Qt's own (always-working) system clipboard.
        """

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if url.scheme() == "copy":
                encoded = url.toString()[len("copy:"):]
                QApplication.clipboard().setText(unquote(encoded))
                return False  # swallow the navigation; nothing actually loads
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class WebBridge:
    """Owns the QWebEngineView chat transcript and pushes JSON events to it.

    Lazily imports QtWebEngineWidgets so the app still runs (falling back to
    QTextBrowser) when WebEngine is not installed.
    """

    def __init__(self, parent_widget, web_dir):
        self.view = QWebEngineView(parent_widget)
        self.view.setPage(_BridgePage(self.view))
        self.view.load(QUrl.fromLocalFile(os.path.join(web_dir, "index.html")))

    def push(self, event):
        """Serialize one event and push it to the web page's __appendEvent.

        json.dumps(serialize_event(event)) double-encodes the JSON so the
        embedded JS receives a STRING literal (which __appendEvent then
        JSON.parses). A bare %s would pass a JS object literal that
        JSON.parse rejects ("[object Object]" -> SyntaxError).
        """
        js = "window.__appendEvent && window.__appendEvent(%s);" % json.dumps(
            serialize_event(event)
        )
        self.view.page().runJavaScript(js)

    def widget(self):
        return self.view


# --- inline-diff helpers ------------------------------------------------------
# These let the desktop UI show "what changed" right in the chat after the agent
# edits a file. They read the file itself (no git needed) so brand-new files and
# non-git folders work too.

# The consolidated tool that changes files. The specific action lives in its
# "operation" argument (write | patch | delete | move).
FILE_EDIT_TOOLS = ("editor",)


def read_text_or_empty(path):
    """Return a file's text, or "" if it doesn't exist yet / can't be read."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def diff_targets(name, args_text):
    """For a file-editing tool call, return (after_path, before_path, label).

    after_path  : the file we read *after* the tool runs (the new state)
    before_path : the file we read *before* it runs (the old state)
    label       : a short, human name to show in the chat header
    Returns None for tools that don't edit a file.
    """
    try:
        args = json.loads(args_text)
    except (json.JSONDecodeError, TypeError):
        return None

    # Only the consolidated "editor" tool changes files now; the specific
    # action is in its "operation" argument.
    if name != "editor":
        return None

    operation = (args.get("operation") or "").lower()

    if operation in ("write", "patch", "delete"):
        path = args.get("path")
        if not path:
            return None
        return (path, path, os.path.basename(path))

    if operation == "move":
        source = args.get("source")
        destination = args.get("destination")
        if not (source and destination):
            return None
        label = f"{os.path.basename(source)} → {os.path.basename(destination)}"
        return (destination, source, label)

    return None


def unified_diff_text(before_text, after_text, label):
    """Build a unified-diff string (or "" if nothing actually changed)."""
    if before_text == after_text:
        return ""
    diff = "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile="a/" + label,
            tofile="b/" + label,
        )
    )
    return diff if diff.strip() else ""


def compact_relative_time(iso_timestamp):
    """A short 'time since' label like the reference: now / 5m / 3h / 2d / 4w / date."""
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return ""
    seconds = (datetime.now() - then).total_seconds()
    if seconds < 60:
        return "now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours}h"
    days = int(seconds // 86400)
    if days < 7:
        return f"{days}d"
    if days < 365:
        return f"{days // 7}w"
    return then.strftime("%Y-%m-%d")


# --- Theme palettes -----------------------------------------------------------
THEMES = {
    "light": {
        "window_bg": "#f9fafb",
        "surface": "#ffffff",
        "border": "#e5e7eb",
        "text": "#1f2937",
        "subtle": "#9ca3af",
        "tool": "#6b7280",
        "user": "#2563eb",
        "agent": "#0f766e",
        "error": "#b91c1c",
        "code_bg": "#f3f4f6",
        "code_fg": "#111827",
        "inline_code": "#be185d",
        "link": "#2563eb",
        "input_bg": "#ffffff",
        "input_border": "#d1d5db",
        "btn_bg": "#2563eb",
        "btn_hover": "#1d4ed8",
        # Diff colors: a green pair for added lines, a red pair for removed ones.
        "diff_add_bg": "#e6ffec",
        "diff_add_fg": "#116329",
        "diff_del_bg": "#ffebe9",
        "diff_del_fg": "#82071e",
        "diff_hunk": "#8250df",   # the "@@ ... @@" location headers (purple)
    },
    "dark": {
        "window_bg": "#1b1b1f",
        "surface": "#27272a",
        "border": "#3f3f46",
        "text": "#e5e7eb",
        "subtle": "#71717a",
        "tool": "#a1a1aa",
        "user": "#60a5fa",
        "agent": "#2dd4bf",
        "error": "#f87171",
        "code_bg": "#18181b",
        "code_fg": "#e5e7eb",
        "inline_code": "#f472b6",
        "link": "#60a5fa",
        "input_bg": "#27272a",
        "input_border": "#3f3f46",
        "btn_bg": "#2563eb",
        "btn_hover": "#1d4ed8",
        # Diff colors: darker, muted green/red so they read well on a dark editor.
        "diff_add_bg": "#12361f",
        "diff_add_fg": "#7ee2a8",
        "diff_del_bg": "#3d1418",
        "diff_del_fg": "#ff9da3",
        "diff_hunk": "#a78bfa",   # the "@@ ... @@" location headers (light purple)
    },
}


class CodeHighlighter(QSyntaxHighlighter):
    """A lightweight syntax highlighter for the read-only file preview.

    Not a full language server — just keywords/strings/numbers/comments/
    decorators via regex, picked by file extension, so a file preview reads
    better than a wall of plain monospace text.
    """

    PY_KEYWORDS = (
        "False None True and as assert async await break class continue def del "
        "elif else except finally for from global if import in is lambda nonlocal "
        "not or pass raise return try while with yield self"
    ).split()
    JS_KEYWORDS = (
        "break case catch class const continue debugger default delete do else "
        "export extends finally for function if import in instanceof let new null "
        "return super switch this throw true false try typeof undefined var void "
        "while with yield async await"
    ).split()
    C_KEYWORDS = (
        "break case char const continue default do double else enum extern float "
        "for goto if int long return short signed sizeof static struct switch "
        "typedef union unsigned void volatile while true false null class public "
        "private protected new this import package func var let"
    ).split()

    # extension -> (keyword list, line-comment marker or None)
    LANG_MAP = {
        ".py": (PY_KEYWORDS, "#"),
        ".pyw": (PY_KEYWORDS, "#"),
        ".js": (JS_KEYWORDS, "//"), ".jsx": (JS_KEYWORDS, "//"),
        ".ts": (JS_KEYWORDS, "//"), ".tsx": (JS_KEYWORDS, "//"),
        ".c": (C_KEYWORDS, "//"), ".h": (C_KEYWORDS, "//"),
        ".cpp": (C_KEYWORDS, "//"), ".hpp": (C_KEYWORDS, "//"),
        ".cs": (C_KEYWORDS, "//"), ".java": (C_KEYWORDS, "//"),
        ".go": (C_KEYWORDS, "//"), ".rs": (C_KEYWORDS, "//"),
        ".sh": ([], "#"), ".bash": ([], "#"), ".yaml": ([], "#"),
        ".yml": ([], "#"), ".toml": ([], "#"), ".ini": ([], ";"),
        ".json": ([], None), ".css": ([], None), ".html": ([], None),
    }

    def __init__(self, document, extension, colors):
        # QSyntaxHighlighter.setDocument() (called by the base constructor)
        # triggers an immediate synchronous rehighlight, which would call our
        # highlightBlock() before any real state exists. Pre-set everything it
        # touches to a safe no-op state first, then fill in the real values
        # (and run the real highlight pass) after super().__init__() returns.
        self.rules = []
        self.comment_prefix = None
        super().__init__(document)
        self._keywords, self.comment_prefix = self.LANG_MAP.get((extension or "").lower(), ([], "#"))
        self.set_colors(colors)

    def set_colors(self, colors):
        """(Re)build the formats from the current theme, and re-run highlighting."""

        def make_format(color_hex, bold=False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_hex))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            return fmt

        self.fmt_keyword = make_format(colors["user"], bold=True)
        self.fmt_string = make_format(colors["diff_add_fg"])
        self.fmt_comment = make_format(colors["subtle"])
        self.fmt_number = make_format(colors["inline_code"])
        self.fmt_decorator = make_format(colors["agent"], bold=True)

        self.rules = [(QRegularExpression(rf"\b{word}\b"), self.fmt_keyword) for word in self._keywords]
        self.rules.append((QRegularExpression(r"\b[0-9][0-9_]*\.?[0-9]*\b"), self.fmt_number))
        self.rules.append((QRegularExpression(r"@\w+"), self.fmt_decorator))
        self.rules.append((QRegularExpression(r'"[^"\n]*"'), self.fmt_string))
        self.rules.append((QRegularExpression(r"'[^'\n]*'"), self.fmt_string))

        self.rehighlight()

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # A line comment overrides everything after it on the line — applied
        # last, and skipped if it looks like it's inside an (already unclosed)
        # quoted string earlier on the same line.
        if self.comment_prefix:
            idx = text.find(self.comment_prefix)
            if idx != -1:
                before = text[:idx]
                if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                    self.setFormat(idx, len(text) - idx, self.fmt_comment)


class AgentWorker(QThread):
    """Runs one agent turn in a background thread and reports events as signals."""

    assistantMessage = Signal(str)
    toolStart = Signal(str, str)     # name, args
    toolResult = Signal(str, str, str)     # name, args, result
    fileDiff = Signal(str, str)            # label, unified diff text
    maxSteps = Signal(str)
    approvalRequested = Signal(str, str)   # command, reason
    questionRequested = Signal(str, list)  # question, options
    reasoningMessage = Signal(str)         # reasoning text
    assistantChunk = Signal(str)          # accumulated assistant text so far
    reasoningChunk = Signal(str)          # accumulated reasoning text so far
    failed = Signal(str)

    def __init__(self, client, model, messages, config, user_text):
        super().__init__()
        self.client = client
        self.model = model
        self.messages = messages
        self.config = config
        self.user_text = user_text
        self._answer = "deny"
        self._answer_ready = threading.Event()
        # Holds the "before" snapshot of a file between a tool's start and result.
        self._pending_diff = None

    def provide_answer(self, answer):
        self._answer = answer
        self._answer_ready.set()

    def _snapshot_before(self, name, args_text):
        """Remember a file's text just before an editing tool changes it."""
        self._pending_diff = None
        targets = diff_targets(name, args_text)
        if targets is None:
            return  # not a file-editing tool
        after_path, before_path, label = targets
        self._pending_diff = {
            "after_path": after_path,
            "before_text": read_text_or_empty(before_path),
            "label": label,
        }

    def _emit_file_diff(self, result):
        """After the tool ran, compare new vs old text and emit a diff to show."""
        pending = self._pending_diff
        self._pending_diff = None
        if pending is None:
            return
        # Skip if the edit didn't actually succeed.
        if (result or "").strip().upper().startswith(("ERROR", "BLOCKED", "CANCELLED")):
            return
        after_text = read_text_or_empty(pending["after_path"])
        diff = unified_diff_text(pending["before_text"], after_text, pending["label"])
        if diff:
            self.fileDiff.emit(pending["label"], diff)

    def run(self):
        try:
            self.messages.append({"role": "user", "content": self.user_text})
            set_current_intent(self.user_text)

            if self.config.get("qt_streaming"):
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
            else:
                gen = run_agent_events(
                    self.client,
                    self.model,
                    self.messages,
                    int(self.config["max_agent_steps"]),
                    self.config["approval_mode"],
                    intent=self.user_text,
                )

            answer_to_send = None
            while True:
                if answer_to_send is None:
                    event = next(gen)
                else:
                    event = gen.send(answer_to_send)
                    answer_to_send = None

                kind = event["type"]

                if kind == "reasoning":
                    self.reasoningMessage.emit(event["content"])
                elif kind == "assistant_message":
                    self.assistantMessage.emit(event["content"])
                elif kind == "tool_start":
                    # The tool hasn't run yet, so snapshot the file's current text.
                    self._snapshot_before(event["name"], event.get("args", ""))
                    self.toolStart.emit(event["name"], event.get("args", ""))
                elif kind == "tool_result":
                    self.toolResult.emit(event["name"], event.get("args", ""), event["result"])
                    # Now the tool has run: diff old vs new and show it in chat.
                    self._emit_file_diff(event["result"])
                elif kind == "approval_request":
                    self._answer_ready.clear()
                    self.approvalRequested.emit(event["command"], event["reason"])
                    self._answer_ready.wait()
                    answer_to_send = self._answer
                elif kind == "question_request":
                    self._answer_ready.clear()
                    self.questionRequested.emit(event["question"], event["options"])
                    self._answer_ready.wait()
                    answer_to_send = self._answer
                elif kind == "max_steps":
                    self.maxSteps.emit(event["content"])
                    break
                elif kind == "done":
                    break

        except StopIteration:
            pass
        except Exception as error:
            self.failed.emit(str(error))


class SkillsToolsDialog(QDialog):
    """A read-only list of the agent's built-in tools and its saved skills."""

    def __init__(self, parent=None, colors=None):
        super().__init__(parent)
        self.setWindowTitle("Skills & Tools")
        self.setMinimumSize(540, 480)

        subtle = colors["subtle"] if colors else "gray"

        rows = [f"<h3 style='margin:0 0 8px 0'>Tools ({len(TOOLS)})</h3>"]
        for tool in TOOLS:
            function = tool.get("function", tool)
            name = escape(function.get("name", ""))
            description = escape((function.get("description", "") or "").strip())
            rows.append(
                f"<p style='margin:0 0 9px 0'><b>{name}</b><br>"
                f"<span style='color:{subtle}'>{description}</span></p>"
            )

        skills_text = list_skills().strip() or "No saved skills yet."
        rows.append("<h3 style='margin:16px 0 8px 0'>Skills</h3>")
        rows.append(
            f"<pre style='white-space:pre-wrap;margin:0;color:{subtle}'>"
            f"{escape(skills_text, br=False)}</pre>"
        )

        view = QTextBrowser()
        view.setHtml("".join(rows))
        if colors:
            view.setStyleSheet(
                f"QTextBrowser {{ background:{colors['surface']}; color:{colors['text']};"
                f" border:1px solid {colors['border']}; border-radius:8px; padding:10px; }}"
            )

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.reject)
        close_buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(view)
        layout.addWidget(close_buttons)


class SettingsDialog(QDialog):
    """A small settings page: pick the model, safety mode, max steps, and theme.

    The dialog only *collects* choices -- it does not change the app itself.
    When the user clicks Save, ChatWindow reads `dialog.values` and applies them.
    Keeping "collect" and "apply" separate makes each part easy to understand.
    """

    APPROVAL_MODES = ["safe_auto", "full_auto"]
    THEME_MODES = [("System", "system"), ("Light", "light"), ("Dark", "dark")]
    REASONING_MODES = [
        ("Default", "default"), ("Low", "low"), ("Medium", "medium"), ("High", "high")
    ]

    def __init__(self, parent, model, approval_mode, max_steps, theme_mode, reasoning):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        # Model: editable so you can type any model id the provider supports.
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        if model and self.model_box.findText(model) == -1:
            self.model_box.addItem(model)
        self.model_box.setCurrentText(model)

        # Reasoning effort — only sent to models/providers that support it.
        self.reasoning_box = QComboBox()
        for label, value in self.REASONING_MODES:
            self.reasoning_box.addItem(label, value)
        reasoning_index = self.reasoning_box.findData(reasoning)
        if reasoning_index != -1:
            self.reasoning_box.setCurrentIndex(reasoning_index)

        # Approval mode (safety): a fixed list of the two valid modes.
        self.approval_box = QComboBox()
        self.approval_box.addItems(self.APPROVAL_MODES)
        if approval_mode in self.APPROVAL_MODES:
            self.approval_box.setCurrentText(approval_mode)

        # Max steps: a number box that only allows sensible values.
        self.steps_box = QSpinBox()
        self.steps_box.setRange(1, 100)
        self.steps_box.setValue(int(max_steps))

        # Theme: show a friendly label but remember the internal value behind it.
        self.theme_box = QComboBox()
        for label, mode in self.THEME_MODES:
            self.theme_box.addItem(label, mode)  # 2nd arg is hidden "item data"
        index = self.theme_box.findData(theme_mode)
        if index != -1:
            self.theme_box.setCurrentIndex(index)

        form = QFormLayout()
        form.addRow("Model", self.model_box)
        form.addRow("Reasoning", self.reasoning_box)
        form.addRow("Approval mode", self.approval_box)
        form.addRow("Max steps", self.steps_box)
        form.addRow("Theme", self.theme_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)  # Save -> dialog closes as "accepted"
        buttons.rejected.connect(self.reject)  # Cancel -> closes as "rejected"

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def values(self):
        """The choices the user made, read by ChatWindow after Save."""
        return {
            "model": self.model_box.currentText().strip(),
            "reasoning": self.reasoning_box.currentData(),
            "approval_mode": self.approval_box.currentText(),
            "max_agent_steps": self.steps_box.value(),
            "theme_mode": self.theme_box.currentData(),
        }


# Keeps extra windows opened via "New window" alive (so they aren't garbage-collected).
OPEN_WINDOWS = []


class TitleBar(QWidget):
    """A custom title bar for the frameless window.

    Dragging an empty part of the bar moves the window (using the OS move, so
    snapping still works); double-clicking it maximizes/restores.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        window = self.window()
        if hasattr(window, "toggle_max_restore"):
            window.toggle_max_restore()
        super().mouseDoubleClickEvent(event)


class SessionRow(QWidget):
    """One session in the left panel: a "•" dot, the name, and a "⋮" menu button.

    Clicking the row opens that session; clicking ⋮ asks for its actions menu.
    """

    openRequested = Signal(str)   # session name
    menuRequested = Signal(str)   # session name

    def __init__(self, name, label, when, is_current, colors, parent=None):
        super().__init__(parent)
        self._name = name
        self.setObjectName("sessionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # no focus outline on the row

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 5, 10, 5)  # 16px left = the indent
        layout.setSpacing(6)
        # Let the name shrink (and elide via clipping) instead of forcing the row
        # wider than the panel, which would push the time / ⋮ off the edge.
        self.setMinimumWidth(0)

        dot = QLabel("•")
        dot.setStyleSheet(f"color:{colors['subtle']};")

        name_label = QLabel(label)
        font = name_label.font()
        font.setBold(is_current)
        name_label.setFont(font)
        name_label.setStyleSheet(f"color:{colors['text']};")
        name_label.setMinimumWidth(0)  # allow it to shrink so the row never overflows

        # Relative time ("2d") between the name and the ⋮ button, like the reference.
        when_label = QLabel(when)
        when_label.setStyleSheet(f"color:{colors['subtle']}; font-size:11px;")

        self._menu_button = QToolButton()
        self._menu_button.setText("⋮")
        self._menu_button.setFixedSize(22, 22)
        self._menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._menu_button.setStyleSheet(
            f"QToolButton {{ border:none; border-radius:4px; font-size:16px;"
            f" color:{colors['subtle']}; }}"
            f"QToolButton:hover {{ background:{colors['window_bg']}; color:{colors['text']}; }}"
        )
        self._menu_button.clicked.connect(lambda: self.menuRequested.emit(self._name))

        layout.addWidget(dot)
        layout.addWidget(name_label, 1)
        layout.addWidget(when_label)
        layout.addWidget(self._menu_button)

        # The current session keeps a persistent highlight; others highlight on hover.
        if is_current:
            self.setStyleSheet(
                f"#sessionRow {{ background:{colors['border']}; border-radius:6px; }}"
            )
        else:
            self.setStyleSheet(
                f"#sessionRow:hover {{ background:{colors['border']}; border-radius:6px; }}"
            )

    def mousePressEvent(self, event):
        # Clicking the row (anywhere except the ⋮ button) opens the session.
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit(self._name)
        super().mousePressEvent(event)


class ChatWindow(QMainWindow):
    def __init__(self, client, model, config):
        super().__init__()
        self.client = client
        self.model = model
        self.config = config
        self.messages = [current_system_message()]
        self.worker = None
        # Messages sent while a turn is still running: the input stays live
        # the whole time, so a send during an active turn queues here instead
        # of starting a second worker; on_finished() drains one entry at a
        # time once the current turn completes.
        self._pending_queue = []
        self.items = []
        self.using_web = False
        # Aggregate "activity" status line (native fallback only — the web
        # bridge path animates its spinner in pure CSS, no timer needed).
        self._status_item = None
        self.spinner_frame = 0
        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(200)
        self.spinner_timer.timeout.connect(self._tick_spinner)
        self.bridge = None
        # True once a message is sent in this session; only then does saving bump
        # its timestamp (so merely viewing a session doesn't reorder the list).
        self._dirty = False

        self.settings = QSettings("SimpleAgent", "SimpleAgent")
        self.theme_mode = self.settings.value("appearance", "system")
        self.c = THEMES["light"]             # replaced by apply_theme()

        self.setWindowTitle("Simple Agent")
        self.resize(1280, 820)

        # Where the agent (and the file tree) operates. Changing it via the
        # folder icon calls os.chdir() so the agent's tools work in that folder.
        self.work_dir = os.getcwd()
        # Remembers which files are already open: abspath -> editor widget,
        # so clicking the same file twice just re-focuses its tab.
        self.open_tabs = {}
        # Cached relative-path listing of work_dir, for "@" mention matching.
        # None means "not built yet" -- built lazily on first use, and reset
        # (rebuilt lazily again) whenever the working directory changes.
        self._file_index = None

        # Sizing for the draggable body splitter (issue: panels resize by drag).
        self._chat_min = 420    # chat never shrinks below this (~1/3 of the width)
        self._panel_min = 160   # a side panel can't be dragged narrower than this
        self._left_w = 260      # remembered open widths (updated as you drag)
        self._right_w = 360

        # The conversation is saved to disk under this name (one file per
        # session, stored per-project in .simpleagent/sessions). The sessions
        # sidebar lists them and lets you resume one.
        self.current_session_name = create_session_name()

        # Frameless window: we paint our own title bar, so hide the OS frame.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        # Build the workspace pieces.
        self.title_bar = self._build_title_bar()
        chat_panel = self._build_chat_panel()
        self.file_panel = self._build_file_panel()
        self.editor_tabs = self._build_editor_tabs()
        self._build_left_panel()
        self._build_right_panel()

        # Body = a draggable splitter: [left panel | chat | right panel]. Drag a
        # panel's inner border to resize it; the chat keeps a minimum width so it
        # can't shrink below ~1/3, and panels can't be dragged shut (use toggles).
        chat_panel.setMinimumWidth(self._chat_min)
        self.left_panel.setMinimumWidth(self._panel_min)
        self.right_panel.setMinimumWidth(self._panel_min)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("bodySplitter")
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(6)  # wide enough to grab and drag easily
        self.body_splitter.addWidget(self.left_panel)
        self.body_splitter.addWidget(chat_panel)
        self.body_splitter.addWidget(self.right_panel)
        self.body_splitter.setStretchFactor(0, 0)  # panels keep their size
        self.body_splitter.setStretchFactor(1, 1)  # chat takes the slack
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.splitterMoved.connect(self._on_splitter_moved)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.title_bar)
        root.addWidget(self.body_splitter, 1)

        self.central = QWidget()
        self.central.setObjectName("central")
        self.central.setLayout(root)
        self.setCentralWidget(self.central)

        # One subtle size grip (bottom-right) resizes the frameless window. The
        # status bar's own grip is turned off so we don't get two stacked triangles.
        self.statusBar().setSizeGripEnabled(False)
        self._size_grip = QSizeGrip(self.central)

        base_font = QFont("Segoe UI")
        base_font.setPointSize(11)
        self.transcript.setFont(base_font)

        self.statusBar().showMessage("Ready")
        self.items.append({"kind": "agent", "text": "Ready. What should I do?"})

        self.refresh_sessions()
        self.update_session_name_button()
        self._position_grips()

        # Live-update when the OS theme changes (only matters in "system" mode).
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                self.on_system_scheme_changed
            )
        except Exception:
            pass

        self.apply_theme()

    # --- layout panels -----------------------------------------------------

    def _build_title_bar(self):
        """Our custom title bar: panel toggles + session button on the left,
        settings and the window controls (min / max / close) on the right."""
        bar = TitleBar()
        self.activity_bar = bar  # kept name so theming can reference it

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(6)

        def tool_button(symbol, tooltip, handler):
            btn = QToolButton()
            btn.setText(symbol)
            btn.setToolTip(tooltip)
            btn.setFixedSize(32, 28)  # every control is the same size
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            return btn

        # Left side: toggle the sessions drawer, then the current-session button.
        self.btn_left = tool_button("▨", "Toggle sessions panel", self.toggle_drawer)
        self.session_name_button = QToolButton()
        self.session_name_button.setToolTip("Session actions")
        self.session_name_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.session_name_button.setMinimumWidth(170)
        self.session_name_button.clicked.connect(self.open_session_menu)

        # Right side: settings, file-panel toggle, and window controls.
        self.btn_settings = tool_button("≡", "Settings", self.open_settings)
        self.btn_right = tool_button("▧", "Toggle project files panel", self.toggle_right_drawer)
        self.btn_min = tool_button("−", "Minimize", self.showMinimized)
        self.btn_max = tool_button("□", "Maximize / restore", self.toggle_max_restore)
        self.btn_close = tool_button("✕", "Close", self.close)
        self.btn_close.setObjectName("closeButton")

        layout.addWidget(self.btn_left)
        layout.addWidget(self.session_name_button)
        layout.addStretch(1)
        layout.addWidget(self.btn_settings)
        layout.addWidget(self.btn_right)
        layout.addSpacing(10)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)
        return bar

    def _build_left_panel(self):
        """The left column: Project folder, New session, Pinned + Past sessions.

        It's a real layout column (not an overlay) that animates its width, so
        opening it pushes the chat to the right instead of covering it.
        """
        panel = QWidget()
        panel.setObjectName("leftPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.left_panel = panel

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(6)

        # Plain "+"-prefixed rows (no icons), per the design.
        self.project_folder_button = QPushButton("＋  Project folder")
        self.project_folder_button.clicked.connect(self.open_folder)
        self.new_session_button = QPushButton("＋  New session")
        self.new_session_button.clicked.connect(self.new_session)

        # All-caps section titles with a small square prefix (rows handle clicks).
        self.pinned_header = QLabel("▪  PINNED SESSIONS")
        self.pinned_list = QListWidget()
        self.pinned_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pinned_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pinned_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.past_header = QLabel("▪  PAST SESSIONS")
        self.past_list = QListWidget()
        self.past_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.past_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.past_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout.addWidget(self.project_folder_button)
        layout.addWidget(self.new_session_button)
        layout.addWidget(self.pinned_header)
        layout.addWidget(self.pinned_list)
        layout.addWidget(self.past_header)
        layout.addWidget(self.past_list, 1)

        self._left_open = False
        panel.hide()  # closed by default; the splitter reveals it when toggled
        return panel

    def _build_right_panel(self):
        """The right column: project file tree + a content viewer. Pushes the chat."""
        panel = QWidget()
        panel.setObjectName("rightPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.right_panel = panel

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # A stack: page 0 is the file tree (full height), page 1 is the file's
        # content (full height). Opening a file swaps to page 1 so the content
        # covers the whole panel — no split, no empty section at the bottom.
        self.right_stack = QStackedWidget()

        self.right_stack.addWidget(self.file_panel)  # page 0

        content_page = QWidget()
        content_layout = QVBoxLayout(content_page)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.back_to_files_button = QPushButton("←  Files")
        self.back_to_files_button.clicked.connect(self.show_file_tree)
        content_layout.addWidget(self.back_to_files_button)
        content_layout.addWidget(self.editor_tabs)
        self.right_stack.addWidget(content_page)  # page 1

        layout.addWidget(self.right_stack)

        self._right_open = False
        panel.hide()  # closed by default; the splitter reveals it when toggled
        return panel

    def _build_file_panel(self):
        """A browsable tree of the working directory; double-click opens a file."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.dir_label = QLabel()
        self.dir_label.setContentsMargins(10, 8, 10, 8)

        # QFileSystemModel reads the real filesystem; QTreeView displays it.
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath(self.work_dir)

        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setRootIndex(self.fs_model.index(self.work_dir))
        self.tree.setHeaderHidden(True)
        for column in (1, 2, 3):  # hide size / type / date-modified columns
            self.tree.hideColumn(column)
        self.tree.doubleClicked.connect(self.on_tree_double_clicked)

        layout.addWidget(self.dir_label)
        layout.addWidget(self.tree)
        self._update_dir_label()
        return panel

    def _build_chat_panel(self):
        """The conversation transcript plus the input row (the original UI)."""
        panel = QWidget()

        self.using_web = _HAS_WEBENGINE
        if self.using_web:
            self.bridge = WebBridge(self, WEB_ASSETS)
            self.transcript = self.bridge.widget()
        else:
            self.bridge = None
            self.transcript = QTextBrowser()
            self.transcript.setOpenLinks(False)
            self.transcript.anchorClicked.connect(self.on_anchor)

        # "+" attach button: opens a menu to attach files/folders/images/URLs
        # as @-reference tokens in the message (see open_attach_menu()).
        self.attach_button = QToolButton()
        self.attach_button.setText("+")
        self.attach_button.setToolTip("Attach files, folders, images, or a URL")
        self.attach_button.setFixedSize(34, 34)
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.clicked.connect(self.open_attach_menu)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the agent... (type @ to reference a file)")
        self.input.returnPressed.connect(self.on_send)
        self.input.textChanged.connect(self._on_input_text_changed)
        self.input.installEventFilter(self)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.on_send)

        # A frameless popup list for "@" inline file-mention autocomplete.
        #
        # NOTE: this intentionally does NOT use Qt.WindowType.Popup. A Popup
        # window grabs the mouse/keyboard at the OS level while shown, and
        # that grab was not always being released cleanly when the popup was
        # closed programmatically (from itemClicked / hide()), which left
        # self.input unable to receive further keystrokes afterward (both
        # "can't edit the inserted @mention" and "input box freezes" were the
        # same underlying grab-not-released bug). Tool + WA_ShowWithoutActivating
        # shows a borderless always-on-top window that never takes OS focus or
        # grabs input, so self.input keeps typing focus at all times; we close
        # it ourselves on Escape/FocusOut/no-match instead of relying on an
        # automatic outside-click grab release.
        self._mention_popup = QListWidget()
        self._mention_popup.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._mention_popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._mention_popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mention_popup.itemClicked.connect(self._insert_mention_selection)
        self._mention_start = -1  # index of the "@" for the mention currently being typed

        row = QHBoxLayout()
        row.addWidget(self.attach_button)
        row.addWidget(self.input)
        row.addWidget(self.send_button)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.transcript)
        layout.addLayout(row)
        return panel

    def _build_editor_tabs(self):
        """Tabbed, read-only viewers for project files (the right side)."""
        tabs = QTabWidget()
        tabs.setTabsClosable(True)
        tabs.setDocumentMode(True)
        tabs.setMovable(True)
        tabs.tabCloseRequested.connect(self.close_tab)
        return tabs

    # --- file tree + editor behavior --------------------------------------

    def not_yet(self):
        self.statusBar().showMessage("That panel is coming in a later phase.")

    def toggle_file_tree(self):
        self.file_panel.setVisible(not self.file_panel.isVisible())

    # --- sessions sidebar --------------------------------------------------

    # --- left panel (sessions) — resizable splitter column -----------------

    def toggle_drawer(self):
        if self._left_open:
            self.close_drawer()
        else:
            self.open_drawer()

    def open_drawer(self):
        if self._left_open:
            return
        self._left_open = True
        self.refresh_sessions()  # show the latest sessions each time it opens
        self.left_panel.show()
        self._apply_panel_widths()

    def close_drawer(self):
        if not self._left_open:
            return
        self._left_open = False
        self.left_panel.hide()
        self._apply_panel_widths()

    # --- right panel (project files) — resizable splitter column -----------

    def toggle_right_drawer(self):
        if self._right_open:
            self.close_right_drawer()
        else:
            self.open_right_drawer()

    def open_right_drawer(self):
        if self._right_open:
            return
        self._right_open = True
        self.right_panel.show()
        self._apply_panel_widths()

    def close_right_drawer(self):
        if not self._right_open:
            return
        self._right_open = False
        self.right_panel.hide()
        self._apply_panel_widths()

    def _apply_panel_widths(self):
        """Set the splitter sizes from open state + the remembered panel widths."""
        total = self.body_splitter.width()
        if total <= 0:
            return
        left = self._left_w if self._left_open else 0
        right = self._right_w if self._right_open else 0
        chat = max(self._chat_min, total - left - right)
        self.body_splitter.setSizes([left, chat, right])

    def _on_splitter_moved(self, _pos, _index):
        """Remember the widths you drag the panels to."""
        sizes = self.body_splitter.sizes()
        if self._left_open and sizes[0] > 0:
            self._left_w = sizes[0]
        if self._right_open and sizes[2] > 0:
            self._right_w = sizes[2]

    # --- window controls (frameless) ---------------------------------------

    def toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_max_button()

    def _update_max_button(self):
        if hasattr(self, "btn_max"):
            self.btn_max.setText("❐" if self.isMaximized() else "□")

    def _position_grips(self):
        size = 16
        self._size_grip.setGeometry(
            self.central.width() - size, self.central.height() - size, size, size
        )
        self._size_grip.setVisible(not self.isMaximized())
        self._size_grip.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_size_grip"):
            self._position_grips()
        self._update_max_button()

    def refresh_sessions(self):
        """Reload sessions into the Pinned and Past lists for this project."""
        if not hasattr(self, "past_list"):
            return
        self.pinned_list.clear()
        self.past_list.clear()
        pinned = self._pinned_names()

        for session in list_saved_sessions():
            is_pinned = session["name"] in pinned
            base = session.get("display_name") or session.get("preview") or "untitled"
            full_when = format_relative_time(session.get("updated_at", ""))
            short_when = compact_relative_time(session.get("updated_at", ""))
            subtitle = f"{full_when} · {session.get('message_count', 0)} msgs"
            is_current = session["name"] == self.current_session_name

            target_list = self.pinned_list if is_pinned else self.past_list
            row = SessionRow(session["name"], base, short_when, is_current, self.c)
            preview = session.get("preview", "")
            row.setToolTip(f"{preview}\n{subtitle}" if preview else subtitle)
            row.openRequested.connect(self._row_open)
            row.menuRequested.connect(self._session_menu)

            item = QListWidgetItem()
            # Width 0 lets each row follow the list's viewport width, so when a
            # scrollbar appears the rows reflow instead of clipping the time / ⋮.
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            target_list.addItem(item)
            target_list.setItemWidget(item, row)

        # Hide the Pinned section entirely when nothing is pinned, and size the
        # pinned list to exactly fit its rows so there's no empty gap below it.
        has_pinned = self.pinned_list.count() > 0
        self.pinned_header.setVisible(has_pinned)
        self.pinned_list.setVisible(has_pinned)
        self._fit_list_height(self.pinned_list)

        self.update_session_name_button()

    def _fit_list_height(self, list_widget):
        """Shrink a list to fit its items (capped) so it doesn't reserve dead space."""
        count = list_widget.count()
        if count == 0:
            list_widget.setFixedHeight(0)
            return
        row = list_widget.sizeHintForRow(0)
        if row <= 0:
            row = 30
        rows = min(count, 8)  # cap so a huge pinned list can't dominate the panel
        list_widget.setFixedHeight(row * rows + 2 * list_widget.frameWidth())

    def _row_open(self, name):
        """A session row was clicked — resume it."""
        if not name or name == self.current_session_name:
            return
        if self.worker is not None:
            self.statusBar().showMessage("Finish the current turn before switching sessions.")
            return
        self.resume_session(name)

    def new_session(self):
        """Save the current conversation, then start an empty one."""
        if self.worker is not None:
            self.statusBar().showMessage("Finish the current turn first.")
            return
        self.save_current_session()
        self.current_session_name = create_session_name()
        self.messages = [current_system_message()]
        self.items = [{"kind": "agent", "text": "Ready. What should I do?"}]
        self._status_item = None
        self.spinner_timer.stop()
        self._dirty = False
        if not self.using_web:
            self.render(scroll_to_end=True)
        self.push_session_to_bridge()
        self.refresh_sessions()
        self.statusBar().showMessage("Started a new session")

    def resume_session(self, name):
        """Load a saved conversation and rebuild the transcript from it."""
        self.save_current_session()  # don't lose the one we're leaving
        try:
            loaded = load_session(name)
        except Exception as error:
            self.statusBar().showMessage(f"Could not open session: {error}")
            return
        # Refresh the system prompt so the resumed chat uses current memory/skills.
        self.messages = apply_system_message(loaded, current_system_message())
        self.current_session_name = name
        self._dirty = False
        self.items = self.items_from_messages(self.messages)
        if not self.items:
            self.items = [{"kind": "agent", "text": "(This session has no messages yet.)"}]
        self._status_item = None
        self.spinner_timer.stop()
        if not self.using_web:
            self.render(scroll_to_end=True)
        self.push_session_to_bridge()
        self.refresh_sessions()
        self.statusBar().showMessage(f"Resumed session: {name}")

    def push_session_to_bridge(self):
        """Clear the web transcript and replay the current items (used by
        new_session / resume_session / change-folder so the QWebEngineView
        reflects the loaded conversation instead of staying frozen)."""
        if not self.bridge:
            return
        self.bridge.push({"type": "reset"})
        for item in self.items:
            kind = item.get("kind")
            if kind == "user":
                self.bridge.push({"type": "user", "text": item.get("text", "")})
            elif kind == "agent":
                self.bridge.push({"type": "assistant_message", "content": item.get("text", ""), "streaming": False})
            elif kind == "status":
                self.bridge.push({"type": "status", "counts": item.get("counts", {})})
            elif kind == "diff":
                self.bridge.push({"type": "diff", "path": item.get("path", ""), "diff": item.get("diff", "")})
            elif kind == "error":
                self.bridge.push({"type": "error", "text": item.get("text", "")})
            # 'reasoning' items are not replayed — they were ephemeral.

    def items_from_messages(self, messages):
        """Turn saved chat messages back into transcript items for display.

        Tool calls within a turn are collapsed into one frozen aggregate status
        line (matching the live behavior), rather than one row per call. (Tool
        results and inline diffs are live-only and aren't stored, so they
        simply don't reappear on resume.)
        """
        items = []
        turn_counts = {}

        def flush_turn():
            if turn_counts:
                items.append({"kind": "status", "active": False, "counts": dict(turn_counts)})
                turn_counts.clear()

        for message in messages:
            role = message.get("role")
            if role == "user":
                flush_turn()
                items.append({"kind": "user", "text": message.get("content", "")})
            elif role == "assistant":
                content = message.get("content") or ""
                if content.strip():
                    items.append({"kind": "agent", "text": content})
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    category, count = classify_tool_call(
                        function.get("name", ""), function.get("arguments", "")
                    )
                    turn_counts[category] = turn_counts.get(category, 0) + count

        flush_turn()
        return items

    def save_current_session(self):
        """Persist the current conversation — only when it actually changed.

        Saving rewrites the session's 'updated_at', which floats it to the top of
        the (last-used-first) list. We skip saving an unchanged session so that
        simply opening/leaving one doesn't reorder the list under you.
        """
        if self._dirty and len(self.messages) > 1:
            try:
                save_session(self.current_session_name, self.model, self.messages)
                self._dirty = False
            except Exception as error:
                self.statusBar().showMessage(f"Could not save session: {error}")

    # --- session-name button + actions menu --------------------------------

    def _label_for(self, name):
        for session in list_saved_sessions():
            if session["name"] == name:
                return session.get("display_name") or session.get("preview") or "untitled"
        return "untitled"

    def _current_session_label(self):
        for session in list_saved_sessions():
            if session["name"] == self.current_session_name:
                return session.get("display_name") or session.get("preview") or "untitled"
        return "New session"

    def update_session_name_button(self):
        if not hasattr(self, "session_name_button"):
            return
        label = self._current_session_label()
        if len(label) > 22:
            label = label[:21] + "…"
        self.session_name_button.setText(f"  {label}  ▾")

    def _pinned_names(self):
        value = self.settings.value("pinned_sessions", [])
        if isinstance(value, str):
            value = [value] if value else []
        return set(value or [])

    def _is_pinned(self, name):
        return name in self._pinned_names()

    def _toggle_pin(self, name):
        pinned = self._pinned_names()
        if name in pinned:
            pinned.discard(name)
            self.statusBar().showMessage("Session unpinned")
        else:
            pinned.add(name)
            self.statusBar().showMessage("Session pinned")
        self.settings.setValue("pinned_sessions", list(pinned))
        self.refresh_sessions()

    def _menu_css(self):
        c = self.c
        return (
            f"QMenu {{ background:{c['surface']}; color:{c['text']};"
            f" border:1px solid {c['border']}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 18px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{c['btn_bg']}; color:white; }}"
            f"QMenu::separator {{ height:1px; background:{c['border']}; margin:4px 6px; }}"
        )

    def open_session_menu(self):
        """Title-bar session button: actions on the *current* session."""
        anchor = self.session_name_button.mapToGlobal(
            QPoint(0, self.session_name_button.height())
        )
        self._session_menu(self.current_session_name, anchor)

    def _session_menu(self, name, pos=None):
        """Actions menu for one session (from the title bar or a row's ⋮ button)."""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_css())
        act_pin = menu.addAction("Unpin" if self._is_pinned(name) else "Pin")
        act_copy = menu.addAction("Copy ID")
        act_export = menu.addAction("Export")
        act_rename = menu.addAction("Rename")
        menu.addSeparator()
        act_delete = menu.addAction("Delete")

        chosen = menu.exec(pos if pos is not None else QCursor.pos())
        if chosen is None:
            return
        if chosen is act_pin:
            self._toggle_pin(name)
        elif chosen is act_copy:
            self._copy_session_id(name)
        elif chosen is act_export:
            self._export_session_json(name)
        elif chosen is act_rename:
            self._rename_session(name)
        elif chosen is act_delete:
            self._delete_session(name)

    def _copy_session_id(self, name):
        QApplication.clipboard().setText(name)
        self.statusBar().showMessage(f"Copied session id: {name}")

    def _export_session_json(self, name):
        if name == self.current_session_name:
            self.save_current_session()
            messages = self.messages
        else:
            try:
                messages = load_session(name)
            except Exception:
                messages = []
        path, _ = QFileDialog.getSaveFileName(
            self, "Export session", f"{name}.json", "JSON files (*.json)"
        )
        if not path:
            return
        data = {"name": name, "model": self.model, "messages": messages}
        try:
            Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"Exported session to {path}")
        except OSError as error:
            self.statusBar().showMessage(f"Export failed: {error}")

    def _rename_session(self, name):
        if name == self.current_session_name:
            self.save_current_session()  # make sure the file exists before renaming
        text, ok = QInputDialog.getText(
            self, "Rename session", "Session name:", text=self._label_for(name)
        )
        if not ok or not text.strip():
            return
        rename_session(name, text.strip())
        self.refresh_sessions()
        self.statusBar().showMessage(f"Renamed session to: {text.strip()}")

    def _delete_session(self, name):
        box = QMessageBox(self)
        box.setWindowTitle("Delete session")
        box.setText(f"Delete this session permanently?\n\n{self._label_for(name)}")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        delete_session_file(name)
        pinned = self._pinned_names()
        pinned.discard(name)
        self.settings.setValue("pinned_sessions", list(pinned))
        if name == self.current_session_name:
            self.new_session()  # start fresh after deleting the open session
        else:
            self.refresh_sessions()
        self.statusBar().showMessage("Session deleted")

    def _update_dir_label(self):
        name = os.path.basename(self.work_dir.rstrip("/\\")) or self.work_dir
        self.dir_label.setText(name)
        self.dir_label.setToolTip(self.work_dir)

    def open_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose working directory", self.work_dir
        )
        if not chosen:
            return  # user cancelled the picker
        self.save_current_session()  # save into the OLD project before leaving it
        self.work_dir = chosen
        os.chdir(chosen)  # so the agent's file tools operate in this folder
        self.fs_model.setRootPath(chosen)
        self.tree.setRootIndex(self.fs_model.index(chosen))
        self._update_dir_label()
        self._file_index = None  # rebuilt lazily for the new folder, on next @-mention

        # Sessions are stored per-project, so start a fresh one in the new folder
        # and show that project's saved sessions.
        self.current_session_name = create_session_name()
        self.messages = [current_system_message()]
        self.items = [{"kind": "agent", "text": "Ready. What should I do?"}]
        self._status_item = None
        self.spinner_timer.stop()
        self._dirty = False
        if not self.using_web:
            self.render(scroll_to_end=True)
        self.push_session_to_bridge()
        self.refresh_sessions()
        self.statusBar().showMessage(f"Working directory: {chosen}")

    def on_tree_double_clicked(self, index):
        path = self.fs_model.filePath(index)
        if os.path.isfile(path):
            self.open_file_in_tab(path)

    def open_file_in_tab(self, path):
        path = os.path.abspath(path)

        # Already open? Just bring its tab to the front.
        if path in self.open_tabs:
            self.editor_tabs.setCurrentWidget(self.open_tabs[path])
            self.show_file_content()
            return

        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = "[Binary file - cannot display as text]"
        except OSError as error:
            text = f"[Could not open file: {error}]"

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        # No wrap: code reads better with real line breaks preserved (scrolls
        # horizontally instead of reflowing lines, which garbles indentation).
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFont("Consolas", 11))
        editor.setProperty("file_path", path)  # remember which file this is

        # Lightweight syntax highlighting, picked by extension. Kept as an
        # attribute so apply_theme() can recolor it when the theme changes.
        editor.highlighter = CodeHighlighter(editor.document(), os.path.splitext(path)[1], self.c)

        index = self.editor_tabs.addTab(editor, os.path.basename(path))
        self.editor_tabs.setTabToolTip(index, path)
        self.editor_tabs.setCurrentIndex(index)
        self.open_tabs[path] = editor
        self._style_editor(editor)
        self.show_file_content()  # cover the whole right panel with the content

    def show_file_tree(self):
        self.right_stack.setCurrentIndex(0)

    def show_file_content(self):
        self.right_stack.setCurrentIndex(1)

    def close_tab(self, index):
        widget = self.editor_tabs.widget(index)
        path = widget.property("file_path")
        self.open_tabs.pop(path, None)
        self.editor_tabs.removeTab(index)
        # Back to the file tree once the last file is closed.
        if self.editor_tabs.count() == 0:
            self.show_file_tree()

    def _style_editor(self, editor):
        c = self.c
        editor.setStyleSheet(
            f"QPlainTextEdit {{ background:{c['code_bg']}; color:{c['code_fg']};"
            f" border:none; padding:8px; }}"
        )
        highlighter = getattr(editor, "highlighter", None)
        if highlighter is not None:
            highlighter.set_colors(c)  # also re-runs highlighting with the new theme

    # --- inline diff rendering --------------------------------------------

    def diff_stats(self, diff_text):
        """Count added / removed lines so the header can show '+5 −2'."""
        added = removed = 0
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return added, removed

    def diff_body_html(self, diff_text):
        """Colored, monospace HTML for an expanded inline diff (one <pre> per line).

        Each line is colored by its first character, the way git/GitHub do it:
          +  added line   -> green      -  removed line -> red
          @@ hunk header  -> purple     file/meta lines -> muted
        """
        c = self.c
        rows = []
        for line in diff_text.splitlines():
            safe = escape(line, br=False) or "&nbsp;"  # keep empty lines visible

            if line.startswith(("+++", "---")):
                style = f'color:{c["subtle"]}'                         # file headers
            elif line.startswith("@@"):
                style = f'color:{c["diff_hunk"]};font-weight:bold'     # hunk header
            elif line.startswith("+"):
                style = f'background:{c["diff_add_bg"]};color:{c["diff_add_fg"]}'
            elif line.startswith("-"):
                style = f'background:{c["diff_del_bg"]};color:{c["diff_del_fg"]}'
            else:
                style = f'color:{c["code_fg"]}'                        # context line

            # <pre> preserves the diff's spacing exactly; margin:0 keeps lines tight.
            rows.append(f'<pre style="margin:0;{style}">{safe}</pre>')

        return (
            f'<div style="margin:4px 0 0 18px;font-family:Consolas,monospace;'
            f'font-size:12px;background:{c["code_bg"]};padding:8px">{"".join(rows)}</div>'
        )

    # --- theming -----------------------------------------------------------

    def build_menu(self):
        # The "hamburger" (☰) sits at the very top-left and opens the slide-out
        # navigation drawer. Settings now lives inside that drawer.
        menu_action = QAction("☰", self)
        menu_action.triggered.connect(self.toggle_drawer)
        self.menuBar().addAction(menu_action)

    def open_settings(self):
        dialog = SettingsDialog(
            self,
            model=self.model,
            approval_mode=self.config.get("approval_mode", "safe_auto"),
            max_steps=self.config.get("max_agent_steps", 20),
            theme_mode=self.theme_mode,
            reasoning=self.config.get("reasoning", "default"),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return  # user clicked Cancel -> change nothing

        values = dialog.values

        # 1) Update the in-memory config. The next AgentWorker reads these keys,
        #    so the change takes effect on your next message (no restart needed).
        self.config["model"] = values["model"]
        self.config["approval_mode"] = values["approval_mode"]
        self.config["max_agent_steps"] = values["max_agent_steps"]
        self.config["reasoning"] = values["reasoning"]

        # 2) Apply the model live: the next worker is built with self.model, and
        #    tool-internal LLM calls use whatever set_llm() was last given.
        self.model = values["model"]
        set_llm(self.client, self.model)
        set_reasoning_effort(values["reasoning"])  # apply reasoning to the next call

        # 3) Theme has its own apply path (repaints colors + syncs the menu).
        self.set_theme(values["theme_mode"])

        # 4) Persist to agent_config.json so the choices survive a restart.
        save_config(self.config)

        self.statusBar().showMessage("Settings saved")

    def set_theme(self, mode):
        self.theme_mode = mode
        self.settings.setValue("appearance", mode)
        self.apply_theme()

    def on_system_scheme_changed(self, _scheme):
        if self.theme_mode == "system":
            self.apply_theme()

    def resolve_mode(self):
        if self.theme_mode != "system":
            return self.theme_mode
        try:
            if QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return "dark"
        except Exception:
            pass
        return "light"

    def transcript_css(self, c):
        return (
            f"p, li, div, td {{ color: {c['text']}; }}"
            "pre, code { font-family: Consolas, 'Courier New', monospace; }"
            f"pre {{ background-color: {c['code_bg']}; color: {c['code_fg']}; margin: 6px 0; }}"
            f"code {{ background-color: {c['code_bg']}; color: {c['inline_code']}; }}"
            f"a {{ color: {c['link']}; }}"
            f"h1, h2, h3 {{ color: {c['text']}; }}"
        )

    def apply_theme(self):
        c = THEMES[self.resolve_mode()]
        self.c = c

        self.setStyleSheet(
            f"QMenuBar {{ background:{c['window_bg']}; color:{c['text']}; }}"
            f"QMenuBar::item:selected {{ background:{c['border']}; }}"
            f"QMenu {{ background:{c['surface']}; color:{c['text']};"
            f" border:1px solid {c['border']}; }}"
            f"QMenu::item:selected {{ background:{c['btn_bg']}; color:white; }}"
            f"QStatusBar {{ background:{c['window_bg']}; color:{c['subtle']}; }}"
        )
        # A 1px border draws the window edge now that the OS frame is hidden.
        self.central.setStyleSheet(
            f"#central {{ background:{c['window_bg']}; border:1px solid {c['border']}; }}"
        )

        if not self.using_web:
            self.transcript.setStyleSheet(
                f"QTextBrowser {{ background:{c['surface']}; border:1px solid {c['border']};"
                f" border-radius:10px; padding:20px; color:{c['text']}; }}"
            )
        self.input.setStyleSheet(
            f"QLineEdit {{ padding:10px 12px; border:1px solid {c['input_border']};"
            f" border-radius:8px; font-size:14px; background:{c['input_bg']}; color:{c['text']}; }}"
            f"QLineEdit:focus {{ border:1px solid {c['user']}; }}"
        )
        self.send_button.setStyleSheet(
            f"QPushButton {{ padding:10px 20px; border:none; border-radius:8px;"
            f" background:{c['btn_bg']}; color:white; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{c['btn_hover']}; }}"
            f"QPushButton:disabled {{ background:{c['subtle']}; }}"
        )
        self.attach_button.setStyleSheet(
            f"QToolButton {{ border:1px solid {c['input_border']}; border-radius:8px;"
            f" background:{c['input_bg']}; color:{c['text']}; font-size:18px; font-weight:bold; }}"
            f"QToolButton:hover {{ background:{c['border']}; }}"
        )
        self._mention_popup.setStyleSheet(
            f"QListWidget {{ background:{c['surface']}; color:{c['text']};"
            f" border:1px solid {c['border']}; border-radius:8px; padding:4px;"
            f" font-family:Consolas,monospace; font-size:12px; }}"
            f"QListWidget::item {{ padding:4px 8px; border-radius:4px; }}"
            f"QListWidget::item:selected {{ background:{c['btn_bg']}; color:white; }}"
        )

        if not self.using_web:
            self.transcript.document().setDefaultStyleSheet(self.transcript_css(c))

        # --- custom title bar ----------------------------------------------
        self.activity_bar.setStyleSheet(
            f"TitleBar {{ background:{c['window_bg']}; border-bottom:1px solid {c['border']}; }}"
            f"QToolButton {{ border:none; border-radius:6px; color:{c['text']};"
            f" font-size:15px; }}"
            f"QToolButton:hover {{ background:{c['border']}; }}"
            f"QToolButton#closeButton:hover {{ background:#e81123; color:white; }}"
        )
        # The session-name button looks like a pill, distinct from the icon buttons.
        self.session_name_button.setStyleSheet(
            f"QToolButton {{ border:1px solid {c['border']}; border-radius:6px;"
            f" padding:4px 10px; color:{c['text']}; background:{c['surface']};"
            f" font-size:12px; text-align:left; }}"
            f"QToolButton:hover {{ background:{c['border']}; }}"
        )

        # --- file tree, editor tabs ----------------------------------------
        self.file_panel.setStyleSheet(f"background:{c['window_bg']};")
        self.dir_label.setStyleSheet(f"color:{c['subtle']}; font-weight:bold;")
        self.tree.setStyleSheet(
            f"QTreeView {{ background:{c['window_bg']}; color:{c['text']}; border:none; }}"
            f"QTreeView::item:selected {{ background:{c['btn_bg']}; color:white; }}"
        )
        self.back_to_files_button.setStyleSheet(
            f"QPushButton {{ text-align:left; padding:8px 12px; border:none;"
            f" border-bottom:1px solid {c['border']}; background:{c['window_bg']};"
            f" color:{c['text']}; font-size:13px; }}"
            f"QPushButton:hover {{ background:{c['border']}; }}"
        )

        # --- side panels + the draggable divider between them ----------------
        # No side borders on the panels; the 1px splitter handle IS the divider,
        # so both sides look identical and consistent.
        self.left_panel.setStyleSheet(f"#leftPanel {{ background:{c['surface']}; }}")
        self.right_panel.setStyleSheet(f"#rightPanel {{ background:{c['surface']}; }}")
        self.body_splitter.setStyleSheet(
            f"QSplitter#bodySplitter::handle {{ background:{c['border']}; }}"
            f"QSplitter#bodySplitter::handle:hover {{ background:{c['btn_bg']}; }}"
        )
        # All-caps section titles (with the "▪" square prefix), muted. The 8px top
        # padding matches the action buttons so all the gaps are even.
        header_css = (
            f"color:{c['subtle']}; font-size:11px; font-weight:bold;"
            f" letter-spacing:1px; padding:8px 10px 4px 10px;"
        )
        self.pinned_header.setStyleSheet(header_css)
        self.past_header.setStyleSheet(header_css)
        list_button_css = (
            f"QPushButton {{ padding:8px 10px; border:none; border-radius:6px;"
            f" background:transparent; color:{c['text']}; text-align:left; font-size:13px; }}"
            f"QPushButton:hover {{ background:{c['border']}; }}"
        )
        self.new_session_button.setStyleSheet(list_button_css)
        self.project_folder_button.setStyleSheet(list_button_css)
        # The rows are custom widgets (SessionRow), so the list itself is just a
        # transparent, borderless container.
        list_css = (
            f"QListWidget {{ background:transparent; border:none; outline:0; }}"
            f"QListWidget::item {{ border:none; outline:0; }}"
            f"QListWidget::item:selected {{ background:transparent; }}"
            f"QListWidget::item:focus {{ border:none; outline:0; }}"
        )
        self.pinned_list.setStyleSheet(list_css)
        self.past_list.setStyleSheet(list_css)

        # Rebuild the session rows so they pick up the new theme colors.
        if hasattr(self, "past_list"):
            self.refresh_sessions()
        self.editor_tabs.setStyleSheet(
            f"QTabWidget::pane {{ border:1px solid {c['border']}; background:{c['surface']}; }}"
            f"QTabBar::tab {{ background:{c['window_bg']}; color:{c['subtle']};"
            f" padding:6px 12px; border:none; }}"
            f"QTabBar::tab:selected {{ background:{c['surface']}; color:{c['text']}; }}"
        )
        for editor in self.open_tabs.values():
            self._style_editor(editor)

        # Make native dialogs (QMessageBox) follow the theme, if supported (Qt 6.8+).
        try:
            hints = QApplication.styleHints()
            if self.theme_mode == "system":
                hints.setColorScheme(Qt.ColorScheme.Unknown)
            elif self.resolve_mode() == "dark":
                hints.setColorScheme(Qt.ColorScheme.Dark)
            else:
                hints.setColorScheme(Qt.ColorScheme.Light)
        except Exception:
            pass

        if self.bridge:
            self.bridge.push({"type": "theme", "mode": self.resolve_mode()})
        else:
            self.render(scroll_to_end=False)

    # --- rendering ---------------------------------------------------------

    def render_item(self, index, item):
        kind = item["kind"]
        c = self.c

        if kind == "user":
            # A full-width, left-aligned highlighted bar (no "You" label).
            return (
                f'<div style="background:{c["code_bg"]};border-radius:6px;'
                f'padding:8px 12px;margin:0;text-align:left">{escape(item["text"])}</div>'
            )

        if kind == "reasoning":
            # Collapsible reasoning section, dimmed and slightly smaller.
            expanded = item.get("expanded", False)
            arrow = "&#9660;" if expanded else "&#9654;"
            header = (
                f'<a href="toggle:{index}" style="color:{c["subtle"]};text-decoration:none">'
                f'{arrow} <b>Thinking</b></a>'
            )
            html = f'<p style="margin:0;font-size:12px">{header}</p>'
            if expanded:
                body = escape(item["text"], br=False)
                html += f'<pre style="margin:2px 0 0 18px;font-size:12px;color:{c["subtle"]}">{body}</pre>'
            return html

        if kind == "agent":
            # No "Agent" label — just the response text.
            return f'<div style="margin:0">{md_to_html(item["text"])}</div>'

        if kind == "status":
            # A single aggregate line per turn: a spinner + counts by category
            # (e.g. "Read 3 files · Edited 5 files"), instead of one row per
            # tool call. See classify_tool_call/format_activity_counts above.
            counts_text = escape(format_activity_counts(item.get("counts", {})))
            if item.get("active"):
                frame = SPINNER_FRAMES[self.spinner_frame % len(SPINNER_FRAMES)]
                verb = escape(item.get("verb") or "Working")
                line = f'{frame} <span style="color:{c["tool"]}">{verb}&#8230;</span>'
                if counts_text:
                    line += f' <span style="color:{c["subtle"]}">&#183; {counts_text}</span>'
            else:
                if not counts_text:
                    return ""
                line = f'<span style="color:{c["subtle"]}">&#10003; {counts_text}</span>'
            return f'<p style="margin:0;font-size:12px">{line}</p>'

        if kind == "diff":
            # A collapsible "what changed" entry shown after the agent edits a file.
            expanded = item.get("expanded", False)
            arrow = "&#9660;" if expanded else "&#9654;"
            added, removed = self.diff_stats(item["diff"])
            header = (
                f'<a href="toggle:{index}" style="color:{c["tool"]};text-decoration:none">'
                f'{arrow} {escape(item["path"])}</a>'
                f' <span style="color:{c["diff_add_fg"]}">+{added}</span>'
                f' <span style="color:{c["diff_del_fg"]}">&#8722;{removed}</span>'
            )
            html = f'<p style="margin:0;font-size:12px">{header}</p>'
            if expanded:
                html += self.diff_body_html(item["diff"])
            return html

        if kind == "error":
            return f'<p style="color:{c["error"]};margin:0"><b>Error:</b> {escape(item["text"])}</p>'

        return ""

    def render(self, scroll_to_end):
        parts = [self.render_item(index, item) for index, item in enumerate(self.items)]
        inner = '<div style="margin-bottom:18px"></div>'.join(parts)
        # Wrap everything in a div carrying the theme text color, so any text that
        # isn't explicitly colored (e.g. the agent's markdown) inherits it. Qt's
        # QTextBrowser otherwise falls back to a default dark text color.
        body = f'<div style="color:{self.c["text"]}">{inner}</div>'

        scrollbar = self.transcript.verticalScrollBar()
        previous = scrollbar.value()

        self.transcript.setHtml(body)
        self.apply_line_spacing(140)

        if scroll_to_end:
            self.transcript.moveCursor(QTextCursor.MoveOperation.End)
            self.transcript.ensureCursorVisible()
        else:
            scrollbar.setValue(previous)

    def apply_line_spacing(self, percent):
        cursor = self.transcript.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(
            percent, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        cursor.mergeBlockFormat(block_format)
        cursor.clearSelection()

    def add(self, item):
        self.items.append(item)
        if not self.using_web:
            self.render(scroll_to_end=True)

    def add_user(self, text):
        self._finalize_status_item()
        if self.bridge:
            self.bridge.push({"type": "user", "text": text})
        else:
            self.add({"kind": "user", "text": text})

    def add_reasoning(self, text):
        if self.bridge:
            self.bridge.push({"type": "reasoning", "content": text, "streaming": False})
        else:
            # Reasoning shows while the agent is thinking, then is cleared when
            # the final answer arrives (matching Cline / the terminal behaviour).
            self.add({"kind": "reasoning", "text": text, "expanded": True})

    def add_agent(self, text):
        if self.bridge:
            # Drop reasoning blocks handled by the web app itself.
            self.bridge.push({"type": "assistant_message", "content": text, "streaming": False})
        else:
            # Drop any reasoning blocks that were shown while thinking, so the
            # final transcript is clean — only the answer remains.
            self.items = [i for i in self.items if i.get("kind") != "reasoning"]
            self.add({"kind": "agent", "text": text})

    def add_assistant_chunk(self, accumulated):
        if self.bridge:
            self.bridge.push({"type": "assistant_message", "content": accumulated, "streaming": True})

    def add_reasoning_chunk(self, accumulated):
        if self.bridge:
            self.bridge.push({"type": "reasoning", "content": accumulated, "streaming": True})

    def add_tool(self, name, args, result):
        if self.bridge:
            self.bridge.push({"type": "tool_result", "name": name, "args": args, "result": result})
        else:
            item = self._ensure_status_item()
            category, count = classify_tool_call(name, args)
            item["counts"][category] = item["counts"].get(category, 0) + count
            item["active"] = True
            self.render(scroll_to_end=False)

    def add_diff(self, path, diff_text):
        if self.bridge:
            self.bridge.push({"type": "diff", "path": path, "diff": diff_text})
        else:
            self.add({"kind": "diff", "path": path, "diff": diff_text})

    def add_error(self, text):
        self._finalize_status_item()
        if self.bridge:
            self.bridge.push({"type": "error", "text": text})
        else:
            self.add({"kind": "error", "text": text})

    def add_tool_start(self, name, args):
        if self.bridge:
            self.bridge.push({"type": "tool_start", "name": name, "args": args})
        else:
            item = self._ensure_status_item()
            category, _count = classify_tool_call(name, args)
            item["verb"] = ACTIVITY[category]["progress"]
            item["active"] = True
            self.render(scroll_to_end=False)
        self.statusBar().showMessage(f"Running {name}...")

    def _ensure_status_item(self):
        """Get (or start) this turn's aggregate activity line, and start its spinner."""
        if self._status_item is None:
            self._status_item = {"kind": "status", "active": True, "counts": {}, "verb": None}
            self.add(self._status_item)
        if not self.spinner_timer.isActive():
            self.spinner_timer.start()
        return self._status_item

    def _finalize_status_item(self):
        """Freeze the activity line into its final summary (or drop it if empty)."""
        self.spinner_timer.stop()
        item = self._status_item
        self._status_item = None
        if item is None:
            return
        item["active"] = False
        if not format_activity_counts(item.get("counts", {})) and item in self.items:
            self.items.remove(item)
        if not self.using_web:
            self.render(scroll_to_end=False)

    def _tick_spinner(self):
        self.spinner_frame += 1
        if not self.using_web:
            self.render(scroll_to_end=False)

    def on_anchor(self, url):
        link = url.toString()
        if link.startswith("toggle:"):
            index = int(link.split(":", 1)[1])
            self.items[index]["expanded"] = not self.items[index].get("expanded", False)
            if not self.using_web:
                self.render(scroll_to_end=False)
        else:
            QDesktopServices.openUrl(url)

    # --- attachments ("+" menu) and "@" inline file mentions ---------------
    #
    # Both features just insert plain "@token" text into the chat input --
    # there's no special backend parsing. The agent's existing tools
    # (read_files, fetch_web, ...) already handle whatever path/URL text
    # appears in your message, so no changes to the agent core are needed.

    def open_attach_menu(self):
        """The '+' button: a small menu to attach files/folders/images/a URL."""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_css())
        act_files = menu.addAction("📄  Files…")
        act_folder = menu.addAction("📁  Folder…")
        act_images = menu.addAction("🖼  Images…")
        act_paste = menu.addAction("📋  Paste image")
        act_url = menu.addAction("🔗  URL…")

        menu.adjustSize()
        button_top_left = self.attach_button.mapToGlobal(QPoint(0, 0))
        pos = QPoint(button_top_left.x(), button_top_left.y() - menu.sizeHint().height())

        chosen = menu.exec(pos)
        if chosen is act_files:
            self._attach_files()
        elif chosen is act_folder:
            self._attach_folder()
        elif chosen is act_images:
            self._attach_images()
        elif chosen is act_paste:
            self._attach_paste_image()
        elif chosen is act_url:
            self._attach_url()

    def _attach_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files", self.work_dir)
        for path in paths:
            self._insert_attachment_token(self._display_path(path))

    def _attach_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Attach folder", self.work_dir)
        if path:
            self._insert_attachment_token(self._display_path(path))

    def _attach_images(self):
        # Note: the configured model (deepseek-v4-flash) doesn't accept image
        # input, so an attached image is just a path reference like any other
        # file -- not sent as image content. Switch to a vision-capable model
        # on your opencode-go plan first if you want actual image understanding.
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach images", self.work_dir,
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
        )
        for path in paths:
            self._insert_attachment_token(self._display_path(path))

    def _attach_paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            self.statusBar().showMessage("No image on the clipboard")
            return
        folder = Path(self.work_dir) / ".simpleagent" / "pasted_images"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"pasted_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        path = folder / filename
        image.save(str(path), "PNG")
        self._insert_attachment_token(self._display_path(str(path)))
        self.statusBar().showMessage(f"Pasted image saved: {path.name}")

    def _attach_url(self):
        url, ok = QInputDialog.getText(self, "Attach URL", "URL:")
        if ok and url.strip():
            self._insert_attachment_token(url.strip())

    def _display_path(self, path):
        """A path relative to the project folder when possible, else absolute."""
        try:
            rel = os.path.relpath(path, self.work_dir)
            if not rel.startswith(".."):
                return rel.replace(os.sep, "/")
        except ValueError:
            pass  # e.g. a different drive on Windows
        return path

    def _insert_attachment_token(self, token):
        """Insert '@token ' into the chat input at the current cursor position."""
        text = self.input.text()
        pos = self.input.cursorPosition()
        prefix, suffix = text[:pos], text[pos:]
        needs_space_before = bool(prefix) and not prefix.endswith((" ", "\n"))
        insertion = (" " if needs_space_before else "") + f"@{token} "
        self.input.setText(prefix + insertion + suffix)
        self.input.setCursorPosition(len(prefix) + len(insertion))
        self.input.setFocus()

    def _project_files(self, limit=4000):
        """Cached relative-path listing of work_dir, for @-mention matching."""
        if self._file_index is None:
            skip_dirs = {
                ".git", "__pycache__", "node_modules", ".venv", "venv", "dist",
                "build", ".simpleagent", ".pytest_cache", ".idea", ".vscode", ".mypy_cache",
            }
            results = []
            for root, dirs, files in os.walk(self.work_dir):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, self.work_dir).replace(os.sep, "/")
                    results.append(rel)
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
            self._file_index = results
        return self._file_index

    def _on_input_text_changed(self, _text):
        self._update_mention_popup()

    def _active_mention_query(self):
        """Return (at_index, query) for the "@mention" under the cursor, else None."""
        text = self.input.text()
        cursor = self.input.cursorPosition()
        at_index = text.rfind("@", 0, cursor)
        if at_index == -1:
            return None
        between = text[at_index + 1:cursor]
        if any(ch.isspace() for ch in between):
            return None
        return at_index, between

    def _update_mention_popup(self):
        active = self._active_mention_query()
        if active is None:
            self._hide_mention_popup()
            return
        at_index, query = active
        matches = [f for f in self._project_files() if query.lower() in f.lower()][:15]
        if not matches:
            self._hide_mention_popup()
            return
        self._mention_start = at_index
        self._mention_popup.clear()
        self._mention_popup.addItems(matches)
        self._mention_popup.setCurrentRow(0)
        self._position_mention_popup()
        if not self._mention_popup.isVisible():
            self._mention_popup.show()

    def _position_mention_popup(self):
        row_height = self._mention_popup.sizeHintForRow(0) or 22
        height = min(row_height * self._mention_popup.count() + 8, 220)
        self._mention_popup.setFixedHeight(height)
        self._mention_popup.setFixedWidth(max(self.input.width(), 240))
        top_left = self.input.mapToGlobal(QPoint(0, 0))
        self._mention_popup.move(top_left.x(), top_left.y() - height)

    def _hide_mention_popup(self):
        if self._mention_popup.isVisible():
            self._mention_popup.hide()
        self._mention_start = -1

    def _insert_mention_selection(self, item=None):
        if item is None:
            item = self._mention_popup.currentItem()
        if item is None or self._mention_start == -1:
            self._hide_mention_popup()
            return
        text = self.input.text()
        cursor = self.input.cursorPosition()
        chosen = item.text()
        new_text = text[:self._mention_start] + f"@{chosen} " + text[cursor:]
        new_cursor = self._mention_start + len(chosen) + 2  # "@" + text + trailing space
        self._hide_mention_popup()
        self.input.setText(new_text)
        self.input.setCursorPosition(new_cursor)
        # Force real (OS-level) keyboard focus back onto the input. The popup
        # never took the OS grab (WA_ShowWithoutActivating), but the window
        # itself may not be the active one if the click came from elsewhere.
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, obj, event):
        # Forward keyboard nav to the mention popup while it's open (the popup
        # itself has no keyboard focus -- self.input keeps typing focus).
        if obj is self.input:
            if event.type() == QEvent.Type.FocusOut and self._mention_popup.isVisible():
                self._hide_mention_popup()
            elif self._mention_popup.isVisible() and event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key.Key_Down:
                    row = min(self._mention_popup.currentRow() + 1, self._mention_popup.count() - 1)
                    self._mention_popup.setCurrentRow(row)
                    return True
                if key == Qt.Key.Key_Up:
                    row = max(self._mention_popup.currentRow() - 1, 0)
                    self._mention_popup.setCurrentRow(row)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                    self._insert_mention_selection()
                    return True
                if key == Qt.Key.Key_Escape:
                    self._hide_mention_popup()
                    return True
        return super().eventFilter(obj, event)

    # --- interaction -------------------------------------------------------

    def on_send(self):
        self._hide_mention_popup()

        text = self.input.text().strip()
        if not text:
            return

        self.input.clear()

        if self.worker is not None:
            # A turn is already running. The input stays live the whole time
            # (no disabling), so this send is queued and shown now; it's
            # actually dispatched to the agent once the current turn finishes
            # (see on_finished draining self._pending_queue).
            self._pending_queue.append(text)
            self.add_user(text)
            self.statusBar().showMessage(f"Queued ({len(self._pending_queue)} waiting) — sends once the agent is free")
            return

        self._start_turn(text)

    def _start_turn(self, text):
        self.add_user(text)
        self._dirty = True  # a real turn — this session may now move to the top
        self.set_busy(True)
        self.statusBar().showMessage("Thinking...")

        self.worker = AgentWorker(self.client, self.model, self.messages, self.config, text)
        self.worker.assistantMessage.connect(self.add_agent)
        self.worker.assistantChunk.connect(self.add_assistant_chunk)
        self.worker.reasoningChunk.connect(self.add_reasoning_chunk)
        self.worker.toolStart.connect(self.add_tool_start)
        self.worker.toolResult.connect(self.add_tool)
        self.worker.fileDiff.connect(self.add_diff)
        self.worker.maxSteps.connect(self.add_agent)
        self.worker.failed.connect(self.add_error)
        self.worker.approvalRequested.connect(self.on_approval)
        self.worker.questionRequested.connect(self.on_question)
        self.worker.reasoningMessage.connect(self.add_reasoning)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_approval(self, command, reason):
        box = QMessageBox(self)
        box.setWindowTitle("Approval required")
        box.setText(f"Reason: {reason}\n\nCommand:\n{command}")
        allow = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        answer = "approve" if box.clickedButton() is allow else "deny"
        self.worker.provide_answer(answer)

    def on_question(self, question, options):
        box = QMessageBox(self)
        box.setWindowTitle("Agent question")
        box.setText(question)
        buttons = []
        for opt in options:
            buttons.append(box.addButton(opt, QMessageBox.ButtonRole.AcceptRole))
        box.exec()
        chosen = box.clickedButton()
        answer = chosen.text() if chosen else ""
        self.worker.provide_answer(answer)

    def on_finished(self):
        if self.bridge:
            self.bridge.push({"type": "done"})
        else:
            self._finalize_status_item()
        self.worker = None
        # Persist the turn and refresh the sidebar (time / preview may have changed).
        self.save_current_session()
        self.refresh_sessions()

        if self._pending_queue:
            # Messages typed/sent while this turn was running -- dispatch the
            # next one immediately instead of going idle.
            next_text = self._pending_queue.pop(0)
            self._start_turn(next_text)
        else:
            self.set_busy(False)
            self.statusBar().showMessage("Ready")

    def set_busy(self, busy):
        # The input is never disabled: it stays live the whole time so you
        # can keep typing/sending while the agent works (queued turns are
        # dispatched one at a time from on_finished). Send just relabels
        # itself to make the queueing behavior visible.
        self.send_button.setText("Queue" if busy else "Send")
        if not busy:
            # Explicitly reclaim OS-level window/keyboard focus, not just
            # Qt's logical focus widget -- guards against the input looking
            # enabled but not actually receiving keystrokes.
            self.activateWindow()
            self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def closeEvent(self, event):
        # Save the conversation when the window closes so nothing is lost.
        self.save_current_session()
        super().closeEvent(event)


def main():
    load_dotenv(AGENT_HOME / ".env")
    config = load_config()

    # Map provider name to the env variable holding its API key
    PROVIDER_KEY_MAP = {
        "openrouter": "OPENROUTER_API_KEY",
        "opencode-go": "OPENCODE_GO_API_KEY",
    }

    provider = config.get("provider", "openrouter")
    key_name = PROVIDER_KEY_MAP.get(provider, "OPENROUTER_API_KEY")
    api_key = os.getenv(key_name)
    if not api_key:
        raise SystemExit(f"Missing {key_name} in your .env file.")

    client = OpenAI(api_key=api_key, base_url=config["base_url"], timeout=30.0)
    set_llm(client, config["model"])
    set_reasoning_effort(config.get("reasoning", "default"))

    app = QApplication(sys.argv)
    window = ChatWindow(client, config["model"], config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
