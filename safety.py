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
    "uv run",
    "python -m py_compile",
    "python -m unittest",
    "uv sync",
]


APPROVAL_REQUIRED_IF_CONTAINS = [
    ">>",
    " >",
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
    "uv build",
    "npm install",
    "winget",
    "curl",
    "invoke-webrequest",
    "invoke-restmethod",
    "start-process",
    "powershell",
    "cmd /c",
    "reg add",
    "reg delete",
    "net user",
    "set-executionpolicy",
    "schtasks",
    "icacls",
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
    "invoke-expression",
    "iex ",
]

# Maps command stems to intent keywords that make them safe to auto-allow
COMMAND_INTENT_MAP = {
    "git push":     ["push", "github", "remote", "deploy", "upload", "publish", "release"],
    "git pull":     ["pull", "update", "sync", "fetch"],
    "git commit":   ["commit", "save", "checkpoint"],
    "git add":      ["commit", "stage", "save"],
    "git merge":    ["merge", "combine", "integrate"],
    "git checkout": ["checkout", "switch", "branch"],
    "git switch":   ["switch", "branch"],
    "pip install":  ["install", "add", "package", "dependency", "library"],
    "uv add":       ["install", "add", "package", "dependency", "library"],
    "uv remove":    ["remove", "uninstall", "delete package"],
    "npm install":  ["install", "add", "package", "dependency"],
    "winget":       ["install", "download", "get"],
    "uv build":     ["build", "package", "wheel", "release", "dist"],
    "mkdir":        ["create", "make", "new", "setup", "folder", "directory"],
    "git stash":   ["stash", "save", "shelve", "temporary"],
    "copy-item":   ["copy", "duplicate", "backup"],
    "move-item":   ["move", "rename", "relocate"],
    "new-item":    ["create", "new", "make", "touch", "file"],
    "uv sync":     ["sync", "install", "dependencies", "setup"],
}

SAFE_PIPE_COMMANDS = {
    "head", "tail", "grep", "sort", "more", "findstr",
    "select-string", "where-object", "out-string", "measure-object",
}

def pipe_is_safe(command: str) -> bool:
    """True if command uses | but every segment is a known safe command."""
    if "|" not in command:
        return False
    segments = [s.strip() for s in command.split("|")]
    return all(
        starts_with_any(seg, AUTO_ALLOW_PREFIXES) or seg.split()[0] in SAFE_PIPE_COMMANDS
        for seg in segments if seg
    )

def intent_permits(command: str, intent: str) -> bool:
    """Return True if the user's intent makes this command safe to auto-allow."""
    if not intent:
        return False

    lowered_intent = intent.lower()

    for cmd_stem, keywords in COMMAND_INTENT_MAP.items():
        if command.startswith(cmd_stem):
            if any(keyword in lowered_intent for keyword in keywords):
                return True

    return False


def starts_with_any(command: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        if command == prefix or command.startswith(prefix + " "):
            return True

    return False


def normalize_approval_mode(approval_mode: str) -> str:
    if approval_mode in ("safe_auto", "full_auto"):
        return approval_mode

    return "safe_auto"  # note


def decide_command(command: str, approval_mode: str ="safe_auto", intent: str = "") -> CommandDecision:
    cleaned_command = command.strip()
    lowered_command = " ".join(cleaned_command.lower().split())
    approval_mode = normalize_approval_mode(approval_mode)

    if not cleaned_command:
        return CommandDecision("block", "Command is empty")
    
    for phrase in BLOCK_IF_CONTAINS:
        if phrase in lowered_command:
            return CommandDecision("block", f"Dangerous command pattern: {phrase}")

    if approval_mode == "full_auto":
        return CommandDecision("allow", "Full auto approval mode.")
        
    if pipe_is_safe(lowered_command):
        return CommandDecision("allow", "Safe pipe between read-only commands.")
        
    if intent_permits(lowered_command, intent):
        return CommandDecision("allow", f"Permitted by user intent.")
    
    for phrase in APPROVAL_REQUIRED_IF_CONTAINS:
        if phrase in lowered_command:
            return CommandDecision(
                "approval_required", f"Command may change files or system state: {phrase}"
            )

    if starts_with_any(lowered_command, AUTO_ALLOW_PREFIXES):
        return CommandDecision("allow", "Known read-only command.")

    return CommandDecision("approval_required", "Unknown command. Approval required.")
