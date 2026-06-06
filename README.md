# Simple Agent

A compact local coding agent you can point at any project folder. It reads,
edits, and runs code on your machine through a small set of focused tools, with
a safety layer for terminal commands. Bring your own model API key.

## Features

- File tools: list, tree, glob, read (full / range / many), search (regex),
  write, patch, move, delete
- Dev tools: run Python tests, compile-check, git status / diff
- Live web fetch (JSON-aware, with optional model-distilled answers)
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
