#!/usr/bin/env python3
"""
reader.py: Convert Claude Code JSONL session files to readable Markdown.

Usage:
    python3 reader.py <session.jsonl>                  # writes output/<session>.md
    python3 reader.py <session.jsonl> --stdout         # prints to terminal instead
    python3 reader.py <session.jsonl> --verbose        # includes full tool results
    python3 reader.py <session.jsonl> --thinking       # includes thinking blocks
    python3 reader.py <session.jsonl> --tail 20        # show only last N turns
    python3 reader.py <session.jsonl> --head 20        # show only first N turns
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


def resolve_output_path(stem, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, stem + '.md')
    if os.path.exists(path):
        n = 2
        while os.path.exists(os.path.join(output_dir, f'{stem}-{n:02d}.md')):
            n += 1
        path = os.path.join(output_dir, f'{stem}-{n:02d}.md')
    return path


def write_output(jsonl_path, output_dir, verbose=False, thinking=False, tail=None, head=None):
    """Parse and stream-render a session directly to a file."""
    stem = os.path.splitext(os.path.basename(jsonl_path))[0]
    path = resolve_output_path(stem, output_dir)
    with open(path, 'w', encoding='utf-8') as f:
        convert_to_file(jsonl_path, f, verbose=verbose, thinking=thinking, tail=tail, head=head)
    return path


# ── Conversation ordering ────────────────────────────────────────────────────

def _order_by_parent_chain(records):
    """Sort records by following the parentUuid linked list from root to leaf."""
    children = {}
    for r in records:
        p = r.get('parentUuid')
        children.setdefault(p, []).append(r)

    ordered = []
    visited = set()
    # Start from root records (parentUuid is None or points to a uuid not in this set)
    known_uuids = {r.get('uuid') for r in records}
    roots = [
        r for r in records
        if r.get('parentUuid') is None or r.get('parentUuid') not in known_uuids
    ]
    queue = sorted(roots, key=lambda r: r.get('timestamp', ''))

    while queue:
        r = queue.pop(0)
        uid = r.get('uuid')
        if uid in visited:
            continue
        visited.add(uid)
        ordered.append(r)
        next_children = sorted(
            children.get(uid, []),
            key=lambda r: r.get('timestamp', '')
        )
        for child in next_children:
            if child.get('uuid') not in visited:
                queue.insert(0, child)

    # Safety net: append anything not reached by the chain
    reached = {r.get('uuid') for r in ordered}
    for r in records:
        if r.get('uuid') not in reached:
            ordered.append(r)

    return ordered


# ── Core: parse ──────────────────────────────────────────────────────────────

def parse_session(jsonl_path):
    """Load and parse a JSONL session file. Returns a structured dict."""
    raw = []
    skipped = 0
    with open(jsonl_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        print(f'Warning: {skipped} corrupt line(s) skipped in {os.path.basename(jsonl_path)}', file=sys.stderr)

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

    # Order by parentUuid chain (authoritative conversation order)
    deduped = _order_by_parent_chain(deduped)

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

def render_session(parsed, verbose=False, thinking=False, tail=None, head=None, out=None):
    """Render a parsed session dict, writing lines to `out` (a file-like object or list)."""
    if out is None:
        out = []
    _write = out.append if isinstance(out, list) else lambda line: out.write(line + '\n')

    deduped      = parsed['deduped']
    tool_results = parsed['tool_results']
    session_id   = parsed['session_id']
    cwd          = parsed['cwd']
    version      = parsed['version']

    # Collect renderable turns for --tail / --head filtering
    renderable = []
    for r in deduped:
        rtype = r.get('type')
        if rtype == 'system':
            continue
        if rtype == 'user':
            content = r.get('message', {}).get('content', '')
            if is_tool_result_only(content):
                continue
            if not strip_command_tags(extract_text(content)):
                continue
        elif rtype == 'assistant':
            msg = r.get('message', {})
            content = msg.get('content', [])
            has_visible = any(
                isinstance(b, dict) and b.get('type') in ('text', 'tool_use', 'thinking')
                for b in content
            )
            if not has_visible:
                continue
        renderable.append(r)

    truncated_note = None
    total_turns = len(renderable)
    if tail is not None and tail < total_turns:
        renderable = renderable[-tail:]
        truncated_note = f'*Showing last {tail} of {total_turns} turns. Omit `--tail` to see all.*'
    elif head is not None and head < total_turns:
        renderable = renderable[:head]
        truncated_note = f'*Showing first {head} of {total_turns} turns. Omit `--head` to see all.*'

    # ── Header ───────────────────────────────────────────────────────────────
    active_flags = []
    if verbose:
        active_flags.append('`--verbose`')
    if thinking:
        active_flags.append('`--thinking`')
    if tail is not None:
        active_flags.append(f'`--tail {tail}`')
    if head is not None:
        active_flags.append(f'`--head {head}`')
    flags_str = f'**Flags:** {", ".join(active_flags)}  ' if active_flags else '**Flags:** none  '

    for line in ['# Claude Code Session', '']:
        _write(line)
    if session_id:
        _write(f'**Session:** `{session_id}`  ')
    if cwd:
        _write(f'**Working directory:** `{cwd}`  ')
    if version:
        _write(f'**Claude Code version:** `{version}`  ')
    _write('')
    _write(flags_str)
    _write('*Token key: `Xin` = new input · `Xout` = output · `X↩` = cache read*')
    _write('')
    if truncated_note:
        _write(truncated_note)
        _write('')
    _write('***')
    _write('')

    for r in renderable:
        rtype = r.get('type')
        ts    = fmt_time(r.get('timestamp', ''))

        rtype = r.get('type')
        ts    = fmt_time(r.get('timestamp', ''))

        if rtype == 'user':
            content = r.get('message', {}).get('content', '')
            text = strip_command_tags(extract_text(content))
            first, _, rest = text.partition('\n')
            _write('***')
            _write(f'**User** `{ts}` — {first}')
            if rest.strip():
                _write('')
                _write(rest.strip())

        elif rtype == 'assistant':
            msg     = r.get('message', {})
            content = msg.get('content', [])
            usage   = msg.get('usage', {})

            text_parts     = []
            tool_parts     = []
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
            _write('***')

            if thinking_parts:
                for tp in thinking_parts:
                    _write(f'> *{tp}*')
                _write('')

            if text_parts:
                first, _, rest = text_parts[0].partition('\n')
                _write(f'**Assistant** `{ts}`{error_flag} — {first}')
                if rest.strip():
                    _write('')
                    _write(rest.strip())
                for text in text_parts[1:]:
                    _write('')
                    _write(text)
            else:
                first_fmt, _ = tool_parts[0]
                _write(f'**Assistant** `{ts}`{error_flag} — {first_fmt}')
                tool_parts = tool_parts[1:]

            for fmt, _ in tool_parts:
                _write(f'— {fmt}')

            if usage:
                _write(f'*{i}in/{o}out/{c}↩*')

    # ── Summary block ────────────────────────────────────────────────────────
    _write('***')
    _write('')
    _write('## Session Summary')
    _write('')

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
        _write(f'| {label:<{col_w}} | {value} |')

    _write('')
    _write('*Cost estimate based on Sonnet 4.x pricing. Adjust `_COST_*` constants at the top of reader.py for other models.*')

    if parsed['tool_use_counts']:
        _write('')
        _write('**Tool call frequency:** ' + ', '.join(
            f'{name} ×{count}'
            for name, count in sorted(parsed['tool_use_counts'].items(), key=lambda x: -x[1])
        ))

    if isinstance(out, list):
        return '\n'.join(out)
    return None


def convert(jsonl_path, verbose=False, thinking=False, tail=None, head=None):
    parsed = parse_session(jsonl_path)
    return render_session(parsed, verbose=verbose, thinking=thinking, tail=tail, head=head)


def convert_to_file(jsonl_path, file_handle, verbose=False, thinking=False, tail=None, head=None):
    parsed = parse_session(jsonl_path)
    render_session(parsed, verbose=verbose, thinking=thinking, tail=tail, head=head, out=file_handle)


# ── List mode ────────────────────────────────────────────────────────────────

def list_sessions(directory):
    """Recursively find and summarise all .jsonl sessions in a directory."""
    paths = []
    for root, _, files in os.walk(directory, followlinks=False):
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

def batch_convert(paths, verbose=False, thinking=False, tail=None, head=None, to_stdout=False):
    outputs = []
    for path in sorted(paths):
        try:
            if to_stdout:
                result = convert(path, verbose=verbose, thinking=thinking, tail=tail, head=head)
                outputs.append(result)
            else:
                output_dir = os.path.join(os.path.dirname(path), 'output')
                out_path = write_output(path, output_dir, verbose=verbose, thinking=thinking, tail=tail, head=head)
                print(f'Written to {out_path}')
        except Exception as e:
            print(f'ERROR: {path}: {e}', file=sys.stderr)
    if to_stdout:
        print('\n\n---\n\n'.join(outputs))


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    args  = sys.argv[1:]
    flags = set(args)

    # Track indices consumed as values by flags that take a numeric argument
    consumed_value_indices = set()
    for flag in ('--tail', '--head'):
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                consumed_value_indices.add(idx + 1)

    positional = [
        a for i, a in enumerate(args)
        if not a.startswith('--') and i not in consumed_value_indices
    ]

    if not positional or '-h' in flags or '--help' in flags:
        print(__doc__)
        sys.exit(0 if ('-h' in flags or '--help' in flags) else 1)

    to_stdout = '--stdout'   in flags
    verbose   = '--verbose'  in flags
    thinking  = '--thinking' in flags
    list_mode = '--list'     in flags

    # Parse --tail N and --head N
    tail = head = None
    for flag in ('--tail', '--head'):
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                try:
                    val = int(args[idx + 1])
                    if val <= 0:
                        raise ValueError
                    if flag == '--tail':
                        tail = val
                    else:
                        head = val
                except ValueError:
                    print(f'Error: {flag} requires a positive integer', file=sys.stderr)
                    sys.exit(1)

    target = positional[0]

    # Directory mode
    if os.path.isdir(target):
        if list_mode:
            list_sessions(target)
        else:
            paths = []
            for root, _, files in os.walk(target, followlinks=False):
                for fname in files:
                    if fname.endswith('.jsonl'):
                        paths.append(os.path.join(root, fname))
            if not paths:
                print(f'No .jsonl files found under {target}', file=sys.stderr)
                sys.exit(1)
            batch_convert(paths, verbose=verbose, thinking=thinking, tail=tail, head=head, to_stdout=to_stdout)
        return

    # Glob pattern
    if '*' in target or '?' in target:
        paths = [p for p in _glob.glob(target) if p.endswith('.jsonl')]
        if not paths:
            print(f'No .jsonl files matched: {target}', file=sys.stderr)
            sys.exit(1)
        batch_convert(paths, verbose=verbose, thinking=thinking, tail=tail, head=head, to_stdout=to_stdout)
        return

    # Single file
    if to_stdout:
        result = convert(target, verbose=verbose, thinking=thinking, tail=tail, head=head)
        print(result)
    else:
        output_dir = os.path.join(os.getcwd(), 'output')
        out_path = write_output(target, output_dir, verbose=verbose, thinking=thinking, tail=tail, head=head)
        print(f'Written to {out_path}')


if __name__ == '__main__':
    main()
