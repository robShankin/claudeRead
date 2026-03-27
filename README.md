# clauderead

> Turn Claude Code session logs into readable conversation transcripts.

Claude Code saves every session as a `.jsonl` file packed with raw JSON. **clauderead** converts those files into clean, skimmable Markdown — ready to read, share with a teammate, or hand to an AI agent for review.

---

## Quick start

```bash
git clone https://github.com/robShankin/clauderead.git
cd clauderead
python3 reader.py ~/.claude/projects/-Users-you-myproject/abc123.jsonl
```

That's it. No install, no dependencies, no virtual environment — just Python 3.7+.

Output lands in `output/abc123.md`. Run with `--stdout` to print to the terminal instead.

---

## Prerequisites

**Python 3.7 or newer** — nothing else.

```bash
python3 --version
```

No Python? Grab it at [python.org](https://www.python.org/downloads/).

---

## Finding your session files

Claude Code stores sessions here:

| OS | Path |
|----|------|
| macOS / Linux | `~/.claude/projects/` |
| Windows | `%USERPROFILE%\.claude\projects\` |

Each project gets its own subfolder named after its path (slashes → dashes):

```
~/.claude/projects/
  -Users-you-myproject/
    abc123.jsonl        ← one file per session
    def456.jsonl
```

> **Heads up:** `.claude` is a hidden directory. On macOS/Linux use `ls -a ~`; on Windows enable "Show hidden files" in Explorer.

A few things worth knowing:
- One `.jsonl` per session, not per project — you may have many.
- Logs live globally, not inside your git repo.
- Anthropic hasn't committed to keeping this path stable — it could change in a future release.

---

## Usage

### Write to a file (default)

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/abc123.jsonl
```

Creates `output/abc123.md` in your current directory. The `output/` folder is created automatically. Existing files are never overwritten — the name increments instead: `abc123-02.md`, `abc123-03.md`, etc.

---

### Flag reference

| Flag | Effect |
|------|--------|
| `--stdout` | Print to terminal instead of writing a file |
| `--verbose` | Include full tool output (truncated at 1000 chars) |
| `--thinking` | Show Claude's thinking blocks (usually redacted — see note below) |
| `--list` | List all sessions in a directory with metadata; don't convert |
| `--tail N` | Show only the last N turns |
| `--head N` | Show only the first N turns |

Flags can be combined freely:

```bash
python3 reader.py abc123.jsonl --verbose --thinking --stdout
python3 reader.py abc123.jsonl --tail 20 --stdout
```

---

### Print to terminal — `--stdout`

```bash
python3 reader.py abc123.jsonl --stdout
```

### Include full tool results — `--verbose`

```bash
python3 reader.py abc123.jsonl --verbose
```

By default, tool calls show the tool name and key inputs only. `--verbose` adds the full output of each tool call beneath it in a fenced code block. Useful when you need to see exactly what Claude read or executed.

### Include thinking blocks — `--thinking`

```bash
python3 reader.py abc123.jsonl --thinking
```

Shows Claude's internal reasoning blocks when present. In practice, Anthropic redacts thinking content before writing it to the JSONL — the block exists but the text is empty, so `--thinking` will almost always show `*(thinking redacted)*` rather than actual content.

It's still useful: you can see **which turns Claude paused to think through**, even if you can't read the thoughts. Actual content only appears when extended thinking is enabled explicitly via the API.

### Batch convert a directory

```bash
python3 reader.py ~/.claude/projects/-Users-you-myproject/
```

Converts every `.jsonl` file found recursively. Each output file is written to an `output/` folder adjacent to its source file.

### List sessions before converting — `--list`

```bash
python3 reader.py ~/.claude/projects/ --list
```

Prints a table of every session found — filename, date, duration, turn count, and tool call count. Handy for finding the session you want before committing to a conversion.

### Use a glob pattern

```bash
python3 reader.py "~/.claude/projects/-Users-you-myproject/*.jsonl"
```

### Limit output for large sessions — `--tail` / `--head`

```bash
python3 reader.py abc123.jsonl --tail 20   # last 20 turns
python3 reader.py abc123.jsonl --head 20   # first 20 turns
```

The output includes a note showing how many turns were omitted. Token totals and cost in the summary still reflect the **full session**, not just the visible turns.

---

## Output format

Each turn looks like this:

```
***
**User** `12:24:03` — for test purposes ask me a multiple choice question.

***
**Assistant** `12:24:10` — **AskUserQuestion** — What is the capital of France? [Paris / Lyon / Marseille / Bordeaux] → **Paris**
*3in/193out/15624↩*
```

- `***` — turn divider
- **Bold name** + backtick timestamp — who spoke and when
- Token counts at the end of each assistant turn (`in / out / cache-read↩`) — a key is included at the top of every output file
- Tool calls shown inline: tool name, key inputs, and (for interactive tools) the result
- `--verbose` adds a fenced code block with the raw tool output beneath each call
- Turns containing tool errors are flagged with `**[!]**` in the header

Each session ends with a **Session Summary** table:

| Field | What it shows |
|-------|---------------|
| Duration | Wall-clock time from first to last turn |
| Turns | User turns / assistant turns |
| Tool calls | Total count + frequency breakdown (e.g. `Edit ×24, Bash ×18`) |
| Tokens | Input, output, cache read, cache write |
| Estimated cost | Based on Sonnet 4.x pricing — rates are constants at the top of `reader.py`; edit them if they've changed or you're on a different model. Verify current rates at [anthropic.com/pricing](https://www.anthropic.com/pricing) |

---

## Agent sublogs

Claude Code agent workflows generate sublog files where every record is marked as a sidechain. Without special handling, these files produce empty output because the normal filter strips sidechain records.

**clauderead detects this automatically.** If filtering sidechains would leave nothing to render, it falls back to rendering the full file. You can point it at any `.jsonl` in your `projects/` directory — regular sessions and agent sublogs alike — and get readable output.

---

## What gets omitted by default

| Omitted | How to include |
|---------|----------------|
| `file-history-snapshot` records | Not includable — internal bookkeeping only |
| `thinking` blocks | `--thinking` |
| Sidechain records (in mixed files) | Not needed — agent sublog files are rendered in full automatically |
| Raw tool result payloads | `--verbose` |
