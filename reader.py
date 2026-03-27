#!/usr/bin/env python3
"""
reader.py: Convert Claude Code JSONL session files to readable Markdown.

Usage:
    python3 reader.py <session.jsonl>                  # writes output/<session>.md
    python3 reader.py <session.jsonl> --stdout         # prints to terminal instead
    python3 reader.py <session.jsonl> --verbose        # includes full tool results
    python3 reader.py <session.jsonl> --thinking       # includes thinking blocks
    python3 reader.py <directory>                      # batch convert all .jsonl files
    python3 reader.py <directory> --list               # list sessions in directory (recursive)
    python3 reader.py "glob/pattern/*.jsonl"           # batch convert by glob

No dependencies beyond Python 3.7+ stdlib.
"""

import glob as _glob
import json
import os
import re
import sys
from datetime import datetime, timezone


# Approximate Sonnet 4.x pricing per million tokens.
# Verify current rates and adjust if needed: https://www.anthropic.com/pricing
_COST_INPUT        = 3.00
_COST_OUTPUT       = 15.00
_COST_CACHE_READ   = 0.30
_COST_CACHE_WRITE  = 3.75


# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_time(ts):
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime('%H:%M:%S')
    except Exception:
        return ts


def parse_ts(ts):
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def is_tool_result_only(content):
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(b, dict) and b.get('type') == 'tool_result'
        for b in content
    )


def extract_text(content):
    if isinstance(content, str):
        return content.strip()
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text':
            t = block.get('text', '').strip()
            if t:
                texts.append(t)
    return '\n\n'.join(texts)


def strip_command_tags(text):
    text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-name>.*?</command-name>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
    return text.strip()


def render_tool_result(result, max_chars=1000):
    if result is None:
        return ''
    if isinstance(result, str):
        text = result
    elif isinstance(result, (dict, list)):
        text = json.dumps(result, indent=2)
    else:
        text = str(result)
    if len(text) > max_chars:
        text = text[:max_chars] + f'\n… ({len(text) - max_chars} chars truncated)'
    return text


def detect_error(result_entry):
    """Return (is_error, reason) for a tool result entry."""
    if result_entry is None:
        return False, ''
    is_err = result_entry.get('is_error', False)
    value  = result_entry.get('value')
    if is_err:
        return True, 'tool reported error'
    if isinstance(value, dict):
        if value.get('stderr', '').strip():
            return True, 'stderr output'
        if 'error' in value:
            return True, str(value['error'])
    if isinstance(value, str):
        if re.search(r'Exit code [^0\s]', value):
            return True, 'non-zero exit code'
        if re.search(r'(Traceback|Error:|Exception:)', value):
            return True, 'exception in output'
    return False, ''


def format_ask_user_question(tool_input, result_entry):
    questions = tool_input.get('questions', [])
    value = result_entry.get('value') if result_entry else None
    answers = {}
    if isinstance(value, dict):
        answers = value.get('answers', {})

    parts = []
    for q in questions:
        question = q.get('question', '')
        options  = [o.get('label', '') for o in q.get('options', [])]
        answer   = answers.get(question, '')

        line = f'**AskUserQuestion** — {question}'
        if options:
            line += f' [{" / ".join(options)}]'
        if answer:
            line += f' → **{answer}**'
        parts.append(line)

    return '\n'.join(parts), False  # AskUserQuestion is never an error


def format_tool_call(tool_use, tool_results, verbose=False):
    """Return (formatted_str, is_error)."""
    name    = tool_use.get('name', 'Unknown')
    inp     = tool_use.get('input', {})
    tool_id = tool_use.get('id', '')
    entry   = tool_results.get(tool_id)

    if name == 'AskUserQuestion':
        return format_ask_user_question(inp, entry)

    is_err, _ = detect_error(entry)

    summary_parts = []
    for k, v in inp.items():
        if isinstance(v, str) and len(v) < 120:
            summary_parts.append(f'{k}={repr(v)}')
        elif isinstance(v, (int, float, bool)):
            summary_parts.append(f'{k}={v}')
    summary = ', '.join(summary_parts[:3])

    line = f'**Tool: {name}**'
    if summary:
        line += f' — {summary}'

    if verbose and entry is not None:
        result_text = render_tool_result(entry.get('value'))
        if result_text:
            line += f'\n\n```\n{result_text}\n```'

    return line, is_err


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f'{h}h {m:02d}m {s:02d}s'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def write_output(content, stem, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, stem + '.md')
    if os.path.exists(path):
        n = 2
        while os.path.exists(os.path.join(output_dir, f'{stem}-{n:02d}.md')):
            n += 1
        path = os.path.join(output_dir, f'{stem}-{n:02d}.md')
    with open(path, 'w') as f:
        f.write(content)
    return path


# ── Core: parse ──────────────────────────────────────────────────────────────

def parse_session(jsonl_path):
    """Load and parse a JSONL session file. Returns a structured dict."""
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

    records = [
        r for r in raw
        if r.get('type') not in ('file-history-snapshot',)
        and not r.get('isSidechain', False)
    ]

    # Deduplicate assistant records by message.id — keep last (most complete)
    seen_msg_ids = {}
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

    # Build tool result map: tool_use_id -> {'value': ..., 'is_error': bool}
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
                tool_results[tid] = {
                    'value':    tool_use_result if tool_use_result else block.get('content', ''),
                    'is_error': block.get('is_error', False),
                }

    # Accumulate stats
    session_id = None
    cwd = None
    version = None
    timestamps = []
    total_input = total_output = total_cache_read = total_cache_write = 0
    user_turns = assistant_turns = tool_call_count = 0
    tool_use_counts = {}
    seen_for_tokens = set()

    for r in deduped:
        rtype = r.get('type')
        ts = parse_ts(r.get('timestamp', ''))
        if ts:
            timestamps.append(ts)

        if rtype == 'user':
            content = r.get('message', {}).get('content', '')
            if not is_tool_result_only(content):
                text = strip_command_tags(extract_text(content))
                if text:
                    user_turns += 1
                    if session_id is None:
                        session_id = r.get('sessionId')
                        cwd        = r.get('cwd')
                        version    = r.get('version')

        elif rtype == 'assistant':
            msg    = r.get('message', {})
            msg_id = msg.get('id')
            usage  = msg.get('usage', {})
            content = msg.get('content', [])

            has_visible = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    tool_call_count += 1
                    name = block.get('name', 'Unknown')
                    tool_use_counts[name] = tool_use_counts.get(name, 0) + 1
                    has_visible = True
                elif block.get('type') in ('text', 'thinking'):
                    has_visible = True

            if has_visible:
                assistant_turns += 1

            # Count tokens once per unique message.id
            if msg_id and msg_id not in seen_for_tokens:
                seen_for_tokens.add(msg_id)
                total_input       += usage.get('input_tokens', 0)
                total_output      += usage.get('output_tokens', 0)
                total_cache_read  += usage.get('cache_read_input_tokens', 0)
                total_cache_write += usage.get('cache_creation_input_tokens', 0)

    duration_secs = None
    if len(timestamps) >= 2:
        duration_secs = (max(timestamps) - min(timestamps)).total_seconds()

    cost = (
        total_input       * _COST_INPUT +
        total_output      * _COST_OUTPUT +
        total_cache_read  * _COST_CACHE_READ +
        total_cache_write * _COST_CACHE_WRITE
    ) / 1_000_000

    return {
        'path':           jsonl_path,
        'deduped':        deduped,
        'tool_results':   tool_results,
        'session_id':     session_id,
        'cwd':            cwd,
        'version':        version,
        'timestamps':     timestamps,
        'duration_secs':  duration_secs,
        'user_turns':     user_turns,
        'assistant_turns': assistant_turns,
        'tool_call_count': tool_call_count,
        'tool_use_counts': tool_use_counts,
        'total_input':    total_input,
        'total_output':   total_output,
        'total_cache_read':  total_cache_read,
        'total_cache_write': total_cache_write,
        'cost':           cost,
    }


# ── Core: render ─────────────────────────────────────────────────────────────

def render_session(parsed, verbose=False, thinking=False, flags=None):
    """Render a parsed session dict to a Markdown string."""
    deduped      = parsed['deduped']
    tool_results = parsed['tool_results']
    session_id   = parsed['session_id']
    cwd          = parsed['cwd']
    version      = parsed['version']

    out = []

    for r in deduped:
        rtype = r.get('type')
        ts    = fmt_time(r.get('timestamp', ''))

        if rtype == 'system':
            continue

        if rtype == 'user':
            content = r.get('message', {}).get('content', '')
            if is_tool_result_only(content):
                continue
            text = strip_command_tags(extract_text(content))
            if not text:
                continue
            first, _, rest = text.partition('\n')
            out.append('***')
            out.append(f'**User** `{ts}` — {first}')
            if rest.strip():
                out.append('')
                out.append(rest.strip())

        elif rtype == 'assistant':
            msg     = r.get('message', {})
            content = msg.get('content', [])
            usage   = msg.get('usage', {})

            text_parts    = []
            tool_parts    = []  # list of (formatted_str, is_error)
            thinking_parts = []
            turn_has_error = False

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get('type')
                if btype == 'text':
                    t = block.get('text', '').strip()
                    if t:
                        text_parts.append(t)
                elif btype == 'tool_use':
                    fmt, is_err = format_tool_call(block, tool_results, verbose)
                    tool_parts.append((fmt, is_err))
                    if is_err:
                        turn_has_error = True
                elif btype == 'thinking' and thinking:
                    t = block.get('thinking', '').strip()
                    thinking_parts.append(t if t else '*(thinking redacted)*')

            if not text_parts and not tool_parts and not thinking_parts:
                continue

            i = usage.get('input_tokens', 0)
            o = usage.get('output_tokens', 0)
            c = usage.get('cache_read_input_tokens', 0)

            error_flag = ' **[!]**' if turn_has_error else ''
            out.append('***')

            # Thinking blocks first (when enabled)
            if thinking_parts:
                for tp in thinking_parts:
                    out.append(f'> *{tp}*')
                out.append('')

            if text_parts:
                first, _, rest = text_parts[0].partition('\n')
                out.append(f'**Assistant** `{ts}`{error_flag} — {first}')
                if rest.strip():
                    out.append('')
                    out.append(rest.strip())
                for text in text_parts[1:]:
                    out.append('')
                    out.append(text)
            else:
                first_fmt, _ = tool_parts[0]
                out.append(f'**Assistant** `{ts}`{error_flag} — {first_fmt}')
                tool_parts = tool_parts[1:]

            for fmt, _ in tool_parts:
                out.append(f'— {fmt}')

            if usage:
                out.append(f'*{i}in/{o}out/{c}↩*')

    # ── Summary block ────────────────────────────────────────────────────────
    out.append('***')
    out.append('')
    out.append('## Session Summary')
    out.append('')

    rows = []
    if parsed['duration_secs'] is not None:
        rows.append(('Duration', fmt_duration(parsed['duration_secs'])))
    rows.append(('User turns',      str(parsed['user_turns'])))
    rows.append(('Assistant turns', str(parsed['assistant_turns'])))
    rows.append(('Tool calls',      str(parsed['tool_call_count'])))
    rows.append(('Input tokens',    f"{parsed['total_input']:,}"))
    rows.append(('Output tokens',   f"{parsed['total_output']:,}"))
    rows.append(('Cache read',      f"{parsed['total_cache_read']:,}"))
    rows.append(('Cache write',     f"{parsed['total_cache_write']:,}"))
    rows.append(('Est. cost',       f"~${parsed['cost']:.4f}"))

    col_w = max(len(r[0]) for r in rows)
    for label, value in rows:
        out.append(f'| {label:<{col_w}} | {value} |')

    out.append('')
    out.append('*Cost estimate based on Sonnet 4.x pricing. Adjust `_COST_*` constants at the top of reader.py for other models.*')

    if parsed['tool_use_counts']:
        out.append('')
        out.append('**Tool call frequency:** ' + ', '.join(
            f'{name} ×{count}'
            for name, count in sorted(parsed['tool_use_counts'].items(), key=lambda x: -x[1])
        ))

    # ── Header ───────────────────────────────────────────────────────────────
    header = ['# Claude Code Session', '']
    if session_id:
        header.append(f'**Session:** `{session_id}`  ')
    if cwd:
        header.append(f'**Working directory:** `{cwd}`  ')
    if version:
        header.append(f'**Claude Code version:** `{version}`  ')
    active_flags = []
    if verbose:
        active_flags.append('`--verbose`')
    if thinking:
        active_flags.append('`--thinking`')
    flags_str = f'**Flags:** {", ".join(active_flags)}  ' if active_flags else '**Flags:** none  '
    header += ['', flags_str, '*Token key: `Xin` = new input · `Xout` = output · `X↩` = cache read*', '', '***', '']

    return '\n'.join(header + out)


def convert(jsonl_path, verbose=False, thinking=False):
    parsed = parse_session(jsonl_path)
    return render_session(parsed, verbose=verbose, thinking=thinking)


# ── List mode ────────────────────────────────────────────────────────────────

def list_sessions(directory):
    """Recursively find and summarise all .jsonl sessions in a directory."""
    paths = []
    for root, _, files in os.walk(directory):
        for fname in files:
            if fname.endswith('.jsonl'):
                paths.append(os.path.join(root, fname))

    if not paths:
        print(f'No .jsonl files found under {directory}')
        return

    rows = []
    for path in sorted(paths):
        try:
            p = parse_session(path)
        except Exception as e:
            rows.append((os.path.basename(path), '(parse error)', '', '', ''))
            continue
        date = min(p['timestamps']).strftime('%Y-%m-%d %H:%M') if p['timestamps'] else '?'
        dur  = fmt_duration(p['duration_secs']) if p['duration_secs'] is not None else '?'
        rows.append((
            os.path.relpath(path, directory),
            date,
            dur,
            str(p['user_turns'] + p['assistant_turns']),
            str(p['tool_call_count']),
        ))

    headers = ('File', 'Date', 'Duration', 'Turns', 'Tools')
    widths  = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    fmt = '  '.join(f'{{:<{w}}}' for w in widths)
    print(fmt.format(*headers))
    print('  '.join('-' * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


# ── Batch mode ───────────────────────────────────────────────────────────────

def batch_convert(paths, verbose=False, thinking=False, to_stdout=False):
    outputs = []
    for path in sorted(paths):
        try:
            result = convert(path, verbose=verbose, thinking=thinking)
        except Exception as e:
            print(f'ERROR: {path}: {e}', file=sys.stderr)
            continue
        if to_stdout:
            outputs.append(result)
        else:
            output_dir = os.path.join(os.path.dirname(path), 'output')
            stem = os.path.splitext(os.path.basename(path))[0]
            out_path = write_output(result, stem, output_dir)
            print(f'Written to {out_path}')
    if to_stdout:
        print('\n\n---\n\n'.join(outputs))


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    args      = sys.argv[1:]
    flags     = set(args)
    positional = [a for a in args if not a.startswith('--')]

    if not positional or '-h' in flags or '--help' in flags:
        print(__doc__)
        sys.exit(0 if ('-h' in flags or '--help' in flags) else 1)

    to_stdout = '--stdout'   in flags
    verbose   = '--verbose'  in flags
    thinking  = '--thinking' in flags
    list_mode = '--list'     in flags

    target = positional[0]

    # Directory mode
    if os.path.isdir(target):
        if list_mode:
            list_sessions(target)
        else:
            paths = []
            for root, _, files in os.walk(target):
                for fname in files:
                    if fname.endswith('.jsonl'):
                        paths.append(os.path.join(root, fname))
            if not paths:
                print(f'No .jsonl files found under {target}', file=sys.stderr)
                sys.exit(1)
            batch_convert(paths, verbose=verbose, thinking=thinking, to_stdout=to_stdout)
        return

    # Glob pattern
    if '*' in target or '?' in target:
        paths = [p for p in _glob.glob(target) if p.endswith('.jsonl')]
        if not paths:
            print(f'No .jsonl files matched: {target}', file=sys.stderr)
            sys.exit(1)
        batch_convert(paths, verbose=verbose, thinking=thinking, to_stdout=to_stdout)
        return

    # Single file
    result = convert(target, verbose=verbose, thinking=thinking)
    if to_stdout:
        print(result)
    else:
        output_dir = os.path.join(os.getcwd(), 'output')
        stem = os.path.splitext(os.path.basename(target))[0]
        out_path = write_output(result, stem, output_dir)
        print(f'Written to {out_path}')


if __name__ == '__main__':
    main()
