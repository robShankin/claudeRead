#!/usr/bin/env python3
"""
reader.py: Convert Claude Code JSONL session files to readable Markdown.

Usage:
    python3 reader.py <session.jsonl>          # writes <session>.md next to input
    python3 reader.py <session.jsonl> --stdout  # prints to terminal instead

No dependencies beyond Python 3.7+ stdlib.
"""

import json
import re
import sys
from datetime import datetime


def fmt_time(ts):
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime('%H:%M:%S')
    except Exception:
        return ts


def is_tool_result_only(content):
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(b, dict) and b.get('type') == 'tool_result'
        for b in content
    )


def extract_text(content):
    """Extract human-readable text from a content string or array."""
    if isinstance(content, str):
        return content.strip()
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text':
            t = block.get('text', '').strip()
            if t:
                texts.append(t)
    return '\n\n'.join(texts)


def format_ask_user_question(tool_input, tool_result):
    """Format an AskUserQuestion tool call as inline text."""
    questions = tool_input.get('questions', [])
    answers = {}
    if isinstance(tool_result, dict):
        answers = tool_result.get('answers', {})

    parts = []
    for q in questions:
        question = q.get('question', '')
        options = [o.get('label', '') for o in q.get('options', [])]
        answer = answers.get(question, '')

        line = f'**AskUserQuestion** — {question}'
        if options:
            line += f' [{" / ".join(options)}]'
        if answer:
            line += f' → **{answer}**'
        parts.append(line)

    return '\n'.join(parts)


def format_tool_call(tool_use, tool_results):
    """Format a generic tool_use block as inline text."""
    name = tool_use.get('name', 'Unknown')
    inp = tool_use.get('input', {})
    tool_id = tool_use.get('id', '')
    result = tool_results.get(tool_id)

    if name == 'AskUserQuestion':
        return format_ask_user_question(inp, result)

    # Generic: show tool name + up to 3 short key=value params
    summary_parts = []
    for k, v in inp.items():
        if isinstance(v, str) and len(v) < 120:
            summary_parts.append(f'{k}={repr(v)}')
        elif isinstance(v, (int, float, bool)):
            summary_parts.append(f'{k}={v}')
    summary = ', '.join(summary_parts[:3])

    if summary:
        return f'**Tool: {name}** — {summary}'
    return f'**Tool: {name}**'


def strip_command_tags(text):
    """Remove internal Claude Code command metadata tags from user messages."""
    text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-name>.*?</command-name>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
    return text.strip()


def convert(jsonl_path):
    # ── Load ────────────────────────────────────────────────────────────────
    raw = []
    with open(jsonl_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # ── Filter noise ────────────────────────────────────────────────────────
    records = [
        r for r in raw
        if r.get('type') not in ('file-history-snapshot',)
        and not r.get('isSidechain', False)
    ]

    # ── Deduplicate assistant records by message.id (streaming sends chunks)
    # Keep the last (most complete) record for each message.id.
    seen_msg_ids = {}  # message.id -> index in deduped
    deduped = []
    for r in records:
        if r.get('type') == 'assistant':
            msg_id = r.get('message', {}).get('id')
            if msg_id:
                if msg_id in seen_msg_ids:
                    deduped[seen_msg_ids[msg_id]] = r
                    continue
                seen_msg_ids[msg_id] = len(deduped)
        deduped.append(r)

    # ── Build tool result map: tool_use_id -> structured result ─────────────
    tool_results = {}
    for r in deduped:
        if r.get('type') != 'user':
            continue
        content = r.get('message', {}).get('content', '')
        if not is_tool_result_only(content):
            continue
        tool_use_result = r.get('toolUseResult')
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result':
                tid = block.get('tool_use_id', '')
                # Prefer structured toolUseResult when available
                tool_results[tid] = tool_use_result if tool_use_result else block.get('content', '')

    # ── Render ───────────────────────────────────────────────────────────────
    session_id = None
    cwd = None
    version = None
    out = []

    for r in deduped:
        rtype = r.get('type')
        ts = fmt_time(r.get('timestamp', ''))

        if rtype == 'system':
            continue

        if rtype == 'user':
            content = r.get('message', {}).get('content', '')

            # Skip pure tool-result records — they're folded into assistant turns
            if is_tool_result_only(content):
                continue

            text = extract_text(content)
            text = strip_command_tags(text)
            if not text:
                continue

            # Capture session metadata from first real user message
            if session_id is None:
                session_id = r.get('sessionId')
                cwd = r.get('cwd')
                version = r.get('version')

            first, _, rest = text.partition('\n')
            out.append('***')
            out.append(f'**User** `{ts}` — {first}')
            if rest.strip():
                out.append('')
                out.append(rest.strip())

        elif rtype == 'assistant':
            msg = r.get('message', {})
            content = msg.get('content', [])
            usage = msg.get('usage', {})

            text_parts = []
            tool_parts = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get('type')
                if btype == 'text':
                    t = block.get('text', '').strip()
                    if t:
                        text_parts.append(t)
                elif btype == 'tool_use':
                    tool_parts.append(format_tool_call(block, tool_results))
                # 'thinking' blocks skipped in MVP

            if not text_parts and not tool_parts:
                continue

            token_str = ''
            if usage:
                i = usage.get('input_tokens', 0)
                o = usage.get('output_tokens', 0)
                c = usage.get('cache_read_input_tokens', 0)
                token_str = f' · *{i}in/{o}out/{c}↩*'

            out.append('***')
            if text_parts:
                first, _, rest = text_parts[0].partition('\n')
                out.append(f'**Assistant** `{ts}` — {first}')
                if rest.strip():
                    out.append('')
                    out.append(rest.strip())
                for text in text_parts[1:]:
                    out.append('')
                    out.append(text)
            else:
                # tool-only turn: fold first tool inline with label
                first_tool, *remaining_tools = tool_parts
                out.append(f'**Assistant** `{ts}` — {first_tool}')
                tool_parts = remaining_tools

            for tool in tool_parts:
                out.append(f'— {tool}')

            if usage:
                out.append(f'*{i}in/{o}out/{c}↩*')

    # ── Header ───────────────────────────────────────────────────────────────
    header = ['# Claude Code Session', '']
    if session_id:
        header.append(f'**Session:** `{session_id}`  ')
    if cwd:
        header.append(f'**Working directory:** `{cwd}`  ')
    if version:
        header.append(f'**Claude Code version:** `{version}`  ')
    header += ['', '***', '']

    return '\n'.join(header + out)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0 if '-h' in sys.argv or '--help' in sys.argv else 1)

    jsonl_path = sys.argv[1]
    to_stdout = '--stdout' in sys.argv

    result = convert(jsonl_path)

    if to_stdout:
        print(result)
    else:
        import os
        output_dir = os.path.join(os.getcwd(), 'output')
        os.makedirs(output_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(jsonl_path))[0]
        output_path = os.path.join(output_dir, stem + '.md')
        if os.path.exists(output_path):
            n = 2
            while os.path.exists(os.path.join(output_dir, f'{stem}-{n:02d}.md')):
                n += 1
            output_path = os.path.join(output_dir, f'{stem}-{n:02d}.md')
        with open(output_path, 'w') as f:
            f.write(result)
        print(f'Written to {output_path}')


if __name__ == '__main__':
    main()
