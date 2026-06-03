from dataclasses import dataclass


@dataclass
class CommandDecision:
    action: str
    reason: str


AUTO_ALLOW_PREFIXES = [
    "get-childitem",
    "dir",
    "ls",
    "get-content",
    "type",
    "cat",
    "pwd",
    "get-location",
    "git status",
    "git diff",
    "git log",
    "python --version",
    "uv --version",
    "uv pip list",
    "where.exe",
    "select-string",
    "rg",
]


ASK_IF_CONTAINS = [
    ">>",
    ">",
    "|",
    ";",
    "&&",
    "||",
    "new-item",
    "set-content",
    "add-content",
    "out-file",
    "remove-item",
    "del ",
    "erase ",
    "rmdir",
    "mkdir",
    "copy-item",
    "move-item",
    "rename-item",
    "git add",
    "git commit",
    "git checkout",
    "git switch",
    "git merge",
    "git rebase",
    "git pull",
    "git push",
    "git reset",
    "git clean",
    "pip install",
    "uv add",
    "uv remove",
    "npm install",
    "winget",
    "curl",
    "invoke-webrequest",
    "invoke-restmethod",
    "start-process",
    "powershell",
    "cmd /c",
]

BLOCK_IF_CONTAINS = [
    "remove-item -recurse",
    "remove-item -r",
    "rm -rf",
    "rm -r ",
    "rmdir /s",
    "del /s",
    "format ",
    "shutdown",
    "restart-computer",
    "git reset --hard",
    "git clean -fd",
]


def starts_with_any(command: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        if command == prefix or command.startswith(prefix + " "):
            return True

    return False


def decide_command(command: str) -> CommandDecision:
    cleaned_command = command.strip()
    lowered_command = " ".join(cleaned_command.lower().split())

    if not cleaned_command:
        return CommandDecision("block", "Command is empty.")

    for phrase in BLOCK_IF_CONTAINS:
        if phrase in lowered_command:
            return CommandDecision("block", f"Dangerous command pattern: {phrase}")

    if starts_with_any(lowered_command, AUTO_ALLOW_PREFIXES):
        return CommandDecision("allow", "Known read-only command.")

    for phrase in ASK_IF_CONTAINS:
        if phrase in lowered_command:
            return CommandDecision(
                "ask", f"Command may change files or system state: {phrase}"
            )

    return CommandDecision("ask", "Unknown command. Approval required.")
