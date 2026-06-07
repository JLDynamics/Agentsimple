"""Saving, loading, listing, and choosing conversation sessions on disk."""

import json
import os
from datetime import datetime
from pathlib import Path

from config import EXPORTS_DIR, SESSIONS_DIR
from prompt import apply_system_message


def create_session_name() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"session-{timestamp}"


def get_session_path(session_name: str) -> Path:
    return SESSIONS_DIR / f"{session_name}.json"


def delete_session_file(session_name: str) -> bool:
    session_path = get_session_path(session_name)

    if session_path.exists():
        session_path.unlink()
        return True

    return False


def save_session(session_name: str, model_name: str, messages: list[dict]) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    session_path = get_session_path(session_name)

    # Preserve a human-readable name set earlier with /name.
    display_name = ""
    if session_path.exists():
        try:
            display_name = json.loads(
                session_path.read_text(encoding="utf-8")
            ).get("display_name", "")
        except Exception:
            display_name = ""

    session_data = {
        "name": session_name,
        "display_name": display_name,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "project": os.getcwd(),
        "model": model_name,
        "messages": messages,
    }

    session_path.write_text(
        json.dumps(session_data, indent=2),
        encoding="utf-8",
    )


def rename_session(session_name: str, display_name: str) -> bool:
    session_path = get_session_path(session_name)

    if not session_path.exists():
        return False

    try:
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    session_data["display_name"] = display_name
    session_path.write_text(
        json.dumps(session_data, indent=2),
        encoding="utf-8",
    )
    return True


def export_session_markdown(session_name: str) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    display_name = ""
    messages = []
    session_path = get_session_path(session_name)
    if session_path.exists():
        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
            display_name = session_data.get("display_name", "")
            messages = session_data.get("messages", [])
        except Exception:
            pass

    title = display_name or session_name
    exported_at = datetime.now().isoformat(timespec="seconds")

    lines = [
        f"# Session: {title}",
        "",
        f"Project: {os.getcwd()}",
        f"Exported: {exported_at}",
        "",
        "---",
        "",
    ]

    for message in messages:
        role = message.get("role")
        content = (message.get("content") or "").strip()

        if role == "user":
            lines.append("## You\n")
            lines.append(content + "\n")
        elif role == "assistant":
            if content:
                lines.append("## Agent\n")
                lines.append(content + "\n")
            for tool_call in message.get("tool_calls") or []:
                tool_name = tool_call.get("function", {}).get("name", "tool")
                lines.append(f"_[used tool: {tool_name}]_\n")

    export_path = EXPORTS_DIR / f"{session_name}.md"
    export_path.write_text("\n".join(lines), encoding="utf-8")
    return export_path


def load_session(session_name: str) -> list[dict]:
    session_path = get_session_path(session_name)
    session_data = json.loads(session_path.read_text(encoding="utf-8"))
    return session_data["messages"]


def format_relative_time(iso_timestamp: str) -> str:
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return iso_timestamp

    seconds = (datetime.now() - then).total_seconds()

    if seconds < 60:
        return "just now"

    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    days = int(seconds // 86400)
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"

    return then.strftime("%Y-%m-%d")


def session_preview(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = " ".join((message.get("content") or "").split())
            if text:
                return text[:60]

    return ""


def list_saved_sessions() -> list[dict]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    sessions = []

    for session_path in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
            messages = session_data.get("messages", [])
            sessions.append(
                {
                    "name": session_data["name"],
                    "display_name": session_data.get("display_name", ""),
                    "updated_at": session_data.get("updated_at", "unknown"),
                    "message_count": len(messages),
                    "preview": session_preview(messages),
                }
            )
        except Exception:
            continue

    sessions.sort(
        key=lambda session: session.get("updated_at", ""),
        reverse=True,
    )

    return sessions


def pick_session(sessions: list[dict], number_text: str) -> dict | None:
    try:
        index = int(number_text.strip()) - 1
    except ValueError:
        return None

    if 0 <= index < len(sessions):
        return sessions[index]

    return None


def most_recent_session_name() -> str | None:
    # list_saved_sessions already returns newest first.
    sessions = list_saved_sessions()
    return sessions[0]["name"] if sessions else None


def print_session_list(sessions: list[dict], current_session_name: str | None = None) -> None:
    for index, session in enumerate(sessions, start=1):
        marker = "  (current)" if session["name"] == current_session_name else ""
        name = session.get("display_name") or "untitled"
        when = format_relative_time(session["updated_at"])
        preview = session.get("preview", "")
        preview_text = f' - "{preview}"' if preview else ""
        print(
            f"{index}. {name} - {when} "
            f"- {session['message_count']} msgs{preview_text}{marker}"
        )


def resume_chosen_session(target_session: dict, system_message: dict) -> tuple[str, list[dict]]:
    loaded = apply_system_message(load_session(target_session["name"]), system_message)
    print()
    print(f"Resumed session: {target_session['name']}")
    print()
    return target_session["name"], loaded


def continue_most_recent(system_message: dict) -> tuple[str, list[dict]]:
    name = most_recent_session_name()

    if name is None:
        print()
        print("No saved sessions to continue. Starting a new session.")
        print()
        return create_session_name(), [system_message]

    loaded = apply_system_message(load_session(name), system_message)
    print()
    print(f"Continuing most recent session: {name}")
    print()
    return name, loaded


def resume_session_picker(system_message: dict) -> tuple[str, list[dict]]:
    sessions = list_saved_sessions()

    if not sessions:
        print()
        print("No saved sessions in this project. Starting a new session.")
        print()
        return create_session_name(), [system_message]

    print()
    print(f"Resume a session (project: {os.getcwd()}):")
    print_session_list(sessions)

    print()
    print("Type a number to resume that session, or new to start fresh.")

    while True:
        words = input("> ").strip().lower().split()

        if not words or words[0] == "new":
            return create_session_name(), [system_message]

        target_session = pick_session(sessions, words[0])

        if target_session is None:
            print()
            print("Invalid choice. Type a number, or new.")
            print()
            continue

        return resume_chosen_session(target_session, system_message)


def choose_session(
    current_session_name: str,
    model_name: str,
    messages: list[dict],
) -> tuple[str, list[dict]]:
    save_session(current_session_name, model_name, messages)

    while True:
        sessions = list_saved_sessions()

        print()
        print(f"Saved Sessions (project: {os.getcwd()}):")
        print_session_list(sessions, current_session_name)

        print()
        print("Type a number to resume that session, or one of:")
        print("  new              start a new session")
        print("  name <number>    give a session a name")
        print("  export <number>  export a session to markdown")
        print("  delete <number>  delete a session")
        print("  cancel           go back without changes")

        words = input("> ").strip().lower().split()

        if not words or words[0] == "cancel":
            print()
            print("Staying in the current session.")
            print()
            return current_session_name, messages

        keyword = words[0]

        if keyword == "new":
            new_session_name = create_session_name()
            system_message = messages[0]
            return new_session_name, [system_message]

        if keyword in ("delete", "name", "export", "resume"):
            if len(words) < 2:
                print()
                print(f"Add the session number, for example: {keyword} 2")
                print()
                continue

            target_session = pick_session(sessions, words[1])

            if target_session is None:
                print()
                print("No session has that number.")
                print()
                continue

            if keyword == "delete":
                if target_session["name"] == current_session_name:
                    print()
                    print("Cannot delete the current session.")
                    print()
                    continue

                confirm = input(f"Delete {target_session['name']}? (y/n) ").strip().lower()
                if confirm == "y":
                    delete_session_file(target_session["name"])
                    print()
                    print(f"Deleted session: {target_session['name']}")
                    print()
                continue

            if keyword == "name":
                new_name = input("Enter a name for this session: ").strip()
                if new_name:
                    rename_session(target_session["name"], new_name)
                    print()
                    print(f"Named session: {new_name}")
                    print()
                continue

            if keyword == "export":
                export_path = export_session_markdown(target_session["name"])
                print()
                print(f"Exported to: {export_path.resolve()}")
                print()
                continue

            return resume_chosen_session(target_session, messages[0])

        target_session = pick_session(sessions, keyword)

        if target_session is None:
            print()
            print("Invalid choice. Type a number, or new, name <n>, export <n>, delete <n>, cancel.")
            print()
            continue

        return resume_chosen_session(target_session, messages[0])
