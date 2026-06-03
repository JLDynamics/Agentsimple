import subprocess
from datetime import datetime
from pathlib import Path

from safety import decide_command

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_OUTPUT_CHARS = 4000
APPROVED_THIS_SESSION = set()
LOGS_DIR = PROJECT_ROOT / "logs"
TOOL_LOG_FILE = LOGS_DIR / "tool_call.log"


def shorten_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n[Output truncated]"


def resolve_project_path(path: str) -> Path:
    target_path = (PROJECT_ROOT / path).resolve()

    if target_path != PROJECT_ROOT and PROJECT_ROOT not in target_path.parents:
        raise ValueError("Path is outside the project folder.")

    return target_path


def list_files(path: str = ".") -> str:
    try:
        target_path = resolve_project_path(path)

        if not target_path.exists():
            return "ERROR: Path does not exist."

        if not target_path.is_dir():
            return "ERROR: Path is not a folder."

        items = []

        for item in sorted(target_path.iterdir()):
            if item.is_dir():
                items.append(f"[DIR] {item.name}")
            else:
                items.append(f"[FILE] {item.name}")

        if not items:
            return "Folder is empty."

        return "\n".join(items)

    except Exception as error:
        return f"ERROR: {error}"


def read_file(path: str) -> str:
    try:
        target_path = resolve_project_path(path)

        if not target_path.exists():
            return "ERROR: File does not exist."
        if not target_path.is_file():
            return "ERROR: Path is not a file."

        content = target_path.read_text(encoding="utf-8")

        return shorten_output(content)

    except Exception as error:
        return f"ERROR: {error}"


def write_file(path: str, content: str) -> str:
    try:
        target_path = resolve_project_path(path)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

        return f"SUCCESS: Wrote file {path}"

    except Exception as error:
        return f"ERROR: {error}"


def search_files(pattern: str, path: str = ".") -> str:
    try:
        target_path = resolve_project_path(path)

        if not target_path.exists():
            return "ERROR: Path does not exist."

        matches = []

        for file_path in target_path.rglob("*"):
            if not file_path.is_file():
                continue

            if ".venv" in file_path.parts or "__pycache__" in file_path.parts:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")

            except UnicodeDecodeError:
                continue

            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern.lower() in line.lower():
                    relative_path = file_path.relative_to(PROJECT_ROOT)
                    matches.append(f"{relative_path}:{line_number}: {line}")

        if not matches:
            return "No matches found."

        return shorten_output("\n".join(matches))

    except Exception as error:
        return f"ERROR: {error}"


def ask_for_approval(command: str, reason: str) -> bool:
    if command in APPROVED_THIS_SESSION:
        return True

    print()
    print("Approval required.")
    print(f"Reason: {reason}")
    print(f"Command: {command}")

    answer = input(
        "Allow once [y], always this exact command this session [a], deny [N]: "
    )
    answer = answer.strip().lower()

    if answer == "a":
        APPROVED_THIS_SESSION.add(command)
        return True

    return answer == "y"


def log_tool_call(tool_name: str, arguments: str, result: str) -> None:
    LOGS_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().isoformat(timespec="seconds")

    log_entry = (
        f"[{timestamp}] TOOL: {tool_name}\n"
        f"ARGUMENTS: {arguments}\n"
        f"RESULT:\n{shorten_output(result)}\n"
        f"{'-' * 60}\n"
    )

    TOOL_LOG_FILE.write_text(
        TOOL_LOG_FILE.read_text(encoding="utf-8") + log_entry
        if TOOL_LOG_FILE.exists()
        else log_entry,
        encoding="utf-8",
    )


def glob_files(pattern: str, path: str = ".") -> str:
    try:
        target_path = resolve_project_path(path)

        if not target_path.exists():
            return "ERROR: Path does not exist."

        matches = []

        for file_path in target_path.rglob(pattern):
            if ".venv" in file_path.parts or "__pycache__" in file_path.parts:
                continue

            relative_path = file_path.relative_to(PROJECT_ROOT)
            matches.append(str(relative_path))

        if not matches:
            return "No matching files found."

        return shorten_output("\n".join(sorted(matches)))

    except Exception as error:
        return f"ERROR: {error}"


def apply_patch(path: str, replacements: list[dict[str, str]]) -> str:
    try:
        target_path = resolve_project_path(path)

        if not target_path.exists():
            return "ERROR: File does not exist."

        if not target_path.is_file():
            return "ERROR: Path is not a file."

        content = target_path.read_text(encoding="utf-8")
        updated_content = content
        changes_made = 0

        for replacement in replacements:
            old_text = replacement.get("old_text", "")
            new_text = replacement.get("new_text", "")

            if not old_text:
                return "ERROR: Each replacement must include old_text."

            if old_text not in updated_content:
                return f"ERROR: old_text not found: {old_text[:80]}"

            updated_content = updated_content.replace(old_text, new_text, 1)
            changes_made += 1

        target_path.write_text(updated_content, encoding="utf-8")

        return f"SUCCESS: Applied {changes_made} replacement(s) to {path}"

    except Exception as error:
        return f"ERROR: {error}"


def execute_terminal_command(command: str) -> str:
    decision = decide_command(command)

    if decision.action == "block":
        return f"BLOCKED: {decision.reason}"

    if decision.action == "ask":
        approved = ask_for_approval(command, decision.reason)

        if not approved:
            return "CANCELLED: User did not approve the command."

    print(f"[Running command]: {command}")

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode == 0:
            output = result.stdout or "Command completed with no output."
            return "SUCCESS:\n" + shorten_output(output)

        error_output = (
            result.stderr or result.stdout or "Command failed with no output."
        )
        return "ERROR:\n" + shorten_output(error_output)

    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out."

    except Exception as error:
        return f"ERROR: {error}"
