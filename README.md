# Simple Agent

A compact local coding agent you can point at any project folder. It reads,
edits, and runs code on your machine through a small set of focused tools, with
a safety layer for terminal commands. Bring your own model API key.

## Features

- File tools: list, tree, glob, read (with line numbers; full / range / many),
  search (regex, optional file-type filter), write, patch, move, delete
- Dev tools: run Python tests, compile-check, git status / diff / log
- Live web fetch (JSON-aware, with optional model-distilled answers)
- Persistent memory: the agent keeps notes about you (global) and each project
  (project), loaded automatically every session
- Self-growing skills: the agent writes and improves its own reusable runbooks,
  loaded on demand so they stay token-cheap
- Session recall: search past conversations and read any session in full
- Terminal command execution with an approval safety layer
- Sessions saved per project, manual `/compact`, slash commands
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

Slash commands: `/help`, `/status`, `/tools`, `/mode`, `/sessions`, `/compact`,
`/clear`, `/exit`. Press `Ctrl+C` to interrupt a running turn.

## Memory

The agent maintains its own long-term memory, loaded into context every session:

- Global memory (`~/.simpleagent/memory.md`) — durable facts about you, shared
  across all projects
- Project memory (`<project>/.simpleagent/memory.md`) — knowledge about the
  current project

It updates these itself when it learns something lasting, and keeps them
concise. To recall details from earlier conversations, it can search past
sessions and read any one in full.

## Skills

The agent also writes its own reusable skills — short markdown runbooks for
tasks it might repeat — under `~/.simpleagent/skills/` (global) and
`<project>/.simpleagent/skills/` (project). Only the skill names and
descriptions are loaded into context; the full steps load on demand, so skills
stay token-cheap. After a non-trivial task the agent saves a skill, and it
improves existing skills when they were helpful but incomplete — so it gets
faster and more consistent the more you use it.

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
