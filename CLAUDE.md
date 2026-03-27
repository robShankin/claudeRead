# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A tool that converts Claude Code `.jsonl` session files into a human-readable (and agent-parseable) format. Target users are engineers debugging or reviewing their Claude Code workflows — they should not need to understand Anthropic's internal JSONL schema to use this tool.

The output should serve two modes:
1. **Human reading** — a clean, skimmable transcript of a session (messages, tool calls, results, token costs)
2. **Agent review** — structured enough that another LLM can ingest and summarize it without needing schema knowledge

## JSONL Schema (Claude Code session files)

Each line is a JSON object. Key `type` values:

| type | Description |
|------|-------------|
| `user` | A human turn. `message.content` is a string or an array (may include `tool_result` items) |
| `assistant` | A Claude turn. `message.content` is an array of `text`, `tool_use`, or `thinking` blocks |
| `system` | Metadata events (e.g., `stop_hook_summary` for hooks that ran at turn end) |
| `file-history-snapshot` | Internal snapshot; safe to ignore in output |

Every record carries: `uuid`, `parentUuid` (forms a linked list / tree), `timestamp`, `sessionId`, `cwd`, `version`, `isSidechain`.

Assistant records also carry `message.usage` (input/output/cache token counts) and `requestId`.

Tool interactions span two records:
- `assistant` record with a `tool_use` content block (`id`, `name`, `input`)
- `user` record with a `tool_result` content block referencing the same `tool_use_id`

## Output Design Goals

- Show speaker (`User` / `Assistant`) and wall-clock time
- Render tool calls as labeled blocks: tool name, inputs, result
- Summarize token usage per turn or per session
- Skip `file-history-snapshot` records entirely
- Collapse `thinking` blocks (show them as a collapsible or omit by default)
- Preserve conversation order via `parentUuid` chain, not line order

## Tech Stack

- **Language:** Python 3.7+ stdlib only — no dependencies, no install step
- **Entry point:** `reader.py` — single file, runs as `python3 reader.py <file.jsonl>`
- **Output:** Writes `output/<session>.md` by default; `--stdout` prints to terminal; `--verbose` includes full tool results

## Running

```bash
python3 reader.py <session.jsonl>
python3 reader.py <session.jsonl> --stdout
python3 reader.py <session.jsonl> --verbose
```

JSONL files live at `~/.claude/projects/<encoded-path>/<session-id>.jsonl`.

## README

`README.md` is the user-facing doc. After making any change to flags, output format, or behavior, check whether README.md still accurately describes what the tool does. The key sections to verify: **Usage** (flags and their effects), **Output format** (example output), and **What gets omitted**. If any of those are stale, update them.

## Design Constraints

**Shareability is the top priority.** All design decisions should favor the option that makes the tool easiest to run without setup: zero dependencies, single file, no virtual environment or build step required. Never add a third-party dependency for MVP features.
