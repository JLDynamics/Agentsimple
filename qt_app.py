"""A native desktop frontend for the agent, built with PySide6 (Qt).

No web server, no HTML page: this process calls run_agent_events() directly and
renders the events with native OS widgets. The agent core is unchanged.
"""

import json
import os
import sys
import threading

from dotenv import load_dotenv
from openai import OpenAI
from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDesktopServices,
    QFont,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from config import AGENT_HOME, load_config
from prompt import current_system_message
from tools import set_llm, set_current_intent
from agent_events import run_agent_events


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
    },
}


class AgentWorker(QThread):
    """Runs one agent turn in a background thread and reports events as signals."""

    assistantMessage = Signal(str)
    toolStart = Signal(str)
    toolResult = Signal(str, str, str)     # name, args, result
    maxSteps = Signal(str)
    approvalRequested = Signal(str, str)   # command, reason
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

    def provide_answer(self, answer):
        self._answer = answer
        self._answer_ready.set()

    def run(self):
        try:
            self.messages.append({"role": "user", "content": self.user_text})
            set_current_intent(self.user_text)

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

                if kind == "assistant_message":
                    self.assistantMessage.emit(event["content"])
                elif kind == "tool_start":
                    self.toolStart.emit(event["name"])
                elif kind == "tool_result":
                    self.toolResult.emit(event["name"], event.get("args", ""), event["result"])
                elif kind == "approval_request":
                    self._answer_ready.clear()
                    self.approvalRequested.emit(event["command"], event["reason"])
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


class ChatWindow(QMainWindow):
    def __init__(self, client, model, config):
        super().__init__()
        self.client = client
        self.model = model
        self.config = config
        self.messages = [current_system_message()]
        self.worker = None
        self.items = []

        self.settings = QSettings("SimpleAgent", "SimpleAgent")
        self.theme_mode = self.settings.value("appearance", "system")
        self.c = THEMES["light"]             # replaced by apply_theme()

        self.setWindowTitle("Simple Agent")
        self.resize(880, 700)

        self.build_menu()

        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.anchorClicked.connect(self.on_anchor)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the agent...")
        self.input.returnPressed.connect(self.on_send)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.on_send)

        row = QHBoxLayout()
        row.addWidget(self.input)
        row.addWidget(self.send_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.transcript)
        layout.addLayout(row)

        self.central = QWidget()
        self.central.setLayout(layout)
        self.setCentralWidget(self.central)

        base_font = QFont("Segoe UI")
        base_font.setPointSize(11)
        self.transcript.setFont(base_font)

        self.statusBar().showMessage("Ready")
        self.items.append({"kind": "agent", "text": "Ready. What should I do?"})

        # Live-update when the OS theme changes (only matters in "system" mode).
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                self.on_system_scheme_changed
            )
        except Exception:
            pass

        self.apply_theme()

    # --- theming -----------------------------------------------------------

    def build_menu(self):
        menu = self.menuBar().addMenu("Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions = {}
        for label, mode in (("Light", "light"), ("Dark", "dark"), ("System", "system")):
            action = QAction(label, self, checkable=True)
            action.setChecked(mode == self.theme_mode)
            action.triggered.connect(lambda _checked, m=mode: self.set_theme(m))
            group.addAction(action)
            menu.addAction(action)
            self.theme_actions[mode] = action

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
        self.central.setStyleSheet(f"background:{c['window_bg']};")

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

        self.transcript.document().setDefaultStyleSheet(self.transcript_css(c))

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

        self.render(scroll_to_end=False)

    # --- rendering ---------------------------------------------------------

    def render_item(self, index, item):
        kind = item["kind"]
        c = self.c

        if kind == "user":
            return (
                f'<p style="color:{c["user"]};margin:0 0 2px 0"><b>You</b></p>'
                f'<p style="margin:0">{escape(item["text"])}</p>'
            )

        if kind == "agent":
            return (
                f'<p style="color:{c["agent"]};margin:0 0 2px 0"><b>Agent</b></p>'
                f'<div style="margin:0">{md_to_html(item["text"])}</div>'
            )

        if kind == "tool":
            expanded = item.get("expanded", False)
            arrow = "&#9660;" if expanded else "&#9654;"
            target = escape(short_detail(item.get("args", "")))
            call = f"{escape(item['name'])}({target})"
            summary = escape(result_summary(item.get("result", "")))
            header = (
                f'<a href="toggle:{index}" style="color:{c["tool"]};text-decoration:none">'
                f"{arrow} {call}</a>"
                f' <span style="color:{c["subtle"]}">&#183; {summary}</span>'
            )
            html = f'<p style="margin:0;font-size:12px">{header}</p>'
            if expanded:
                full = escape((item["result"] or "")[:4000], br=False)
                html += f'<pre style="margin:2px 0 0 18px;font-size:12px">{full}</pre>'
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
        self.render(scroll_to_end=True)

    def add_user(self, text):
        self.add({"kind": "user", "text": text})

    def add_agent(self, text):
        self.add({"kind": "agent", "text": text})

    def add_tool(self, name, args, result):
        self.add({"kind": "tool", "name": name, "args": args, "result": result})

    def add_error(self, text):
        self.add({"kind": "error", "text": text})

    def on_anchor(self, url):
        link = url.toString()
        if link.startswith("toggle:"):
            index = int(link.split(":", 1)[1])
            self.items[index]["expanded"] = not self.items[index].get("expanded", False)
            self.render(scroll_to_end=False)
        else:
            QDesktopServices.openUrl(url)

    # --- interaction -------------------------------------------------------

    def on_send(self):
        if self.worker is not None:
            return

        text = self.input.text().strip()
        if not text:
            return

        self.input.clear()
        self.add_user(text)
        self.set_busy(True)
        self.statusBar().showMessage("Thinking...")

        self.worker = AgentWorker(self.client, self.model, self.messages, self.config, text)
        self.worker.assistantMessage.connect(self.add_agent)
        self.worker.toolStart.connect(lambda n: self.statusBar().showMessage(f"Running {n}..."))
        self.worker.toolResult.connect(self.add_tool)
        self.worker.maxSteps.connect(self.add_agent)
        self.worker.failed.connect(self.add_error)
        self.worker.approvalRequested.connect(self.on_approval)
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

    def on_finished(self):
        self.set_busy(False)
        self.worker = None
        self.statusBar().showMessage("Ready")

    def set_busy(self, busy):
        self.input.setDisabled(busy)
        self.send_button.setDisabled(busy)
        if not busy:
            self.input.setFocus()


def main():
    load_dotenv(AGENT_HOME / ".env")
    config = load_config()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY in your .env file.")

    client = OpenAI(api_key=api_key, base_url=config["base_url"], timeout=30.0)
    set_llm(client, config["model"])

    app = QApplication(sys.argv)
    window = ChatWindow(client, config["model"], config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
