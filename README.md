# Simple Agent

A compact local coding agent you can point at any project folder. It reads,
edits, and runs code on your machine through a small set of focused tools, with
a safety layer for terminal commands. Bring your own model API key.

## Features

- File tools: list, tree, glob, read (with line numbers; full / range / many),
  search (regex, optional file-type filter), write, patch, move, delete
- Dev tools: run Python tests, compile-check, git status / diff / log
- Live web fetch (JSON-aware, with model-distilled answers on full page content)
- Connection safety: API client requests include a 30-second timeout to prevent infinite freezes
- Persistent memory: the agent keeps notes about you (global) and each project
  (project), loaded automatically every session
- Self-growing skills: the agent writes and improves its own reusable runbooks,
  loaded on demand so they stay token-cheap
- Session recall: search past conversations and read any session in full
- Terminal command execution with an approval safety layer
- Rich terminal UI: welcome banner, a "thinking" spinner, and markdown-rendered
  replies
- Sessions saved per project, manual `/compact`, `/rewind`, slash commands
- Works on whatever folder you launch it in (like a real code agent)

## Requirements

- [uv](https://docs.astral.sh/uv/) installed
- An [OpenRouter](https://openrouter.ai/) API key (or any OpenAI-compatible key)

## Install (one line)

```
uv tool install git+https://github.com/JLDynamics/Agentsimple
```

This installs a `simpleagent` command you can run from any folder.

## Set your API key

The agent reads `OPENROUTER_API_KEY` from your environment. Set it once
(PowerShell):

```
setx OPENROUTER_API_KEY "your-key-here"
```

Open a new terminal afterward so the change takes effect.

## Usage

Go into the project you want to work on, then launch:

```
cd C:\path\to\your-project
simpleagent
```

The agent works on that folder. Its state (sessions, logs) is stored in a
`.simpleagent/` folder inside the project.

To continue previous work instead of starting fresh:

```
simpleagent --resume      # choose a saved session before chat starts
simpleagent --continue    # continue the most recent saved session
```

Short forms are also supported:

```
simpleagent -r
simpleagent -c
```

Slash commands: `/help`, `/status`, `/tools`, `/memory`, `/skills`, `/mode`,
`/sessions`, `/compact`, `/rewind [n]`, `/clear`, `/exit`. Press `Ctrl+C` to
interrupt a running turn.

## Sessions

Sessions are saved per project in `.simpleagent/sessions/`. Use `/sessions` to
resume, start, name, export, or delete sessions from one menu. The list shows
the session name (`untitled` by default), last update time, message count, and a
preview of the first user message. Exports are written as markdown transcripts
under `.simpleagent/exports/`, which makes them easy to hand to another agent.

## Memory

The agent maintains its own long-term memory, loaded into context every session:

- Global memory (`~/.simpleagent/memory.md`) — durable facts about you, shared
  across all projects
- Project memory (`<project>/.simpleagent/memory.md`) — knowledge about the
  current project

It updates these itself when it learns something lasting, keeps them concise,
and prunes facts that become stale or wrong so the memory stays current. To
recall details from earlier conversations, it can search past sessions and read
any one in full.

## Skills

The agent also writes its own reusable skills — short markdown runbooks for
tasks it might repeat — under `~/.simpleagent/skills/` (global) and
`<project>/.simpleagent/skills/` (project). Only the skill names and
descriptions are loaded into context; the full steps load on demand, so skills
stay token-cheap. After a non-trivial task the agent saves a skill, improves
existing skills when they were helpful but incomplete, and deletes skills that
become outdated — so it gets faster and more consistent the more you use it.

## Project structure

| File | Role |
|---|---|
| `main.py` | Entry point, CLI parsing, REPL loop, slash commands |
| `agent.py` | Agent step loop: tool execution and compaction |
| `llm.py` | LLM client: streaming and plain responses |
| `prompt.py` | System prompt builder |
| `config.py` | Settings, tool registry, context window helpers |
| `sessions.py` | Session save/load/list/export |
| `tools.py` | All tool implementations (file, dev, web, memory, skills) |
| `tools_schema.json` | Tool definitions sent to the model |
| `safety.py` | Terminal command approval logic |
| `ui.py` | Rich terminal UI: console, printing, slash-command views |

## Run from source (for development)

```
git clone https://github.com/JLDynamics/Agentsimple
cd Agentsimple
uv sync
```

Create a `.env` file with your key:

```
OPENROUTER_API_KEY=your-key-here
```

Then run:

```
uv run python main.py
```

## Configuration

Settings live in `agent_config.json` (model, approval mode, context window,
etc.). If the file is absent, sensible defaults are used.

## Update / uninstall

```
uv tool upgrade agentsimple
uv tool uninstall agentsimple
```
