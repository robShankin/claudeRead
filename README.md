# clauderead

Turn Claude Code session logs into readable conversation transcripts.

Claude Code saves every session as a `.jsonl` file full of raw JSON. This tool converts those files into clean, skimmable Markdown — useful for reading what happened in a session, sharing with a teammate, or passing to an AI agent for review.

---

## Prerequisites

- **Python 3.7 or newer** — no other dependencies, no packages to install

To check your Python version:
```bash
python3 --version
```

If you don't have Python 3, download it from [python.org](https://www.python.org/downloads/).

---

## Installation

1. Download or clone this repo:
```bash
git clone https://github.com/your-username/clauderead.git
cd clauderead
```

2. That's it. There's nothing to install.

---

## Finding your session files

Claude Code stores session files in a `projects/` directory under its config folder. The location depends on your OS:

**macOS / Linux:**
```
~/.claude/projects/
```

**Windows:**
```
%USERPROFILE%\.claude\projects\
```

Within `projects/`, each project gets its own folder named after its file path (slashes replaced with dashes). For example, a project at `/Users/you/myproject` maps to:
```
~/.claude/projects/-Users-you-myproject/
```

Each `.jsonl` file in that folder is one session, named by its session ID (a UUID). Pass any of these files directly to `reader.py`.

**A few things worth knowing:**
- The `.claude` directory is hidden by default. On macOS/Linux use `ls -a ~` to see it; on Windows enable "Show hidden files" in Explorer.
- There will be multiple `.jsonl` files per project — one per session, not one per project.
- Logs are stored globally, not inside your git repo.
- Anthropic has not guaranteed this path as permanent — the location or structure could change in future versions of Claude Code.

---

## Usage

### Write to a file (default)

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/abc123.jsonl
```

Creates `output/abc123.md` in your current directory. The `output/` folder is created automatically if it doesn't exist. If the file already exists, it increments: `abc123-02.md`, `abc123-03.md`, etc.

### Print to terminal

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/abc123.jsonl --stdout
```

### Include full tool results (verbose mode)

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/abc123.jsonl --verbose
```

By default, tool calls show the tool name and key inputs only. `--verbose` adds the full output of each tool call (truncated at 1000 characters if very long). Useful when you need to see exactly what Claude read or executed.

### Include thinking blocks

```bash
python3 reader.py abc123.jsonl --thinking
```

Shows Claude's internal reasoning blocks when present. In practice, Anthropic redacts thinking content before it's written to the JSONL — the block exists in the log but the text is empty. This means `--thinking` will almost always show `*(thinking redacted)*` rather than actual reasoning text.

It's still useful: you can see **which turns Claude paused to think through**, even if you can't read the thoughts themselves. Actual thinking content would only appear if you were using extended thinking mode explicitly via the API.

### Convert all sessions in a directory (batch mode)

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/
```

Converts every `.jsonl` file found recursively. Each output file is written to an `output/` folder adjacent to its source file.

### List all sessions in a directory

```bash
python3 reader.py ~/.claude/projects/ --list
```

Prints a table of all sessions found recursively — filename, date, duration, turn count, and tool call count. Useful for finding the session you want before converting it.

### Use a glob pattern

```bash
python3 reader.py "~/.claude/projects/-Users-you-myproject/*.jsonl"
```

### Limit output for large sessions

```bash
python3 reader.py abc123.jsonl --tail 20   # last 20 turns only
python3 reader.py abc123.jsonl --head 20   # first 20 turns only
```

Useful when a session is very long. The output file will include a note showing how many turns were omitted. Token totals and cost in the summary still reflect the **full session**, not just the visible turns.

### Combine flags

```bash
python3 reader.py abc123.jsonl --verbose --thinking --stdout
```

---

## Output format

Each turn in the conversation looks like this:

```
***
**User** `12:24:03` — for test purposes ask me a multiple choice question.

***
**Assistant** `12:24:10` — **AskUserQuestion** — What is the capital of France? [Paris / Lyon / Marseille / Bordeaux] → **Paris**
*3in/193out/15624↩*
```

- `***` — turn divider
- **Bold name** + backtick timestamp — who spoke and when
- Token counts at the end of each assistant turn: `in / out / cache-read↩` — a key is included at the top of every output file
- Tool calls shown inline: tool name, key inputs, and (for interactive tools) the result
- `--verbose` adds a fenced code block with the raw tool output beneath each tool call

---

## Output format

Each session ends with a **Session Summary** table showing:
- Duration, turn counts, tool call count
- Token totals (input, output, cache read, cache write)
- Estimated cost based on Sonnet 4.x pricing (rates are constants at the top of `reader.py` — edit them if they've changed or you're on a different model; verify at [anthropic.com/pricing](https://www.anthropic.com/pricing))
- Tool call frequency breakdown (e.g. `Edit ×24, Bash ×18, Read ×11`)

Turns containing tool errors are flagged with `**[!]**` in the turn header.

## What gets omitted by default

- Internal `file-history-snapshot` records
- `thinking` blocks (use `--thinking` to include)
- Sidechain records
- Raw tool result payloads (use `--verbose` to include)
