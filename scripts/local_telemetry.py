#!/usr/bin/env python3
"""Read-only local ACLA observer. No message bodies or reasoning are persisted."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3


def utc():
    return datetime.now(timezone.utc)


def connect(path):
    db = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def seconds(start, end):
    if not start or not end:
        return None
    try:
        return max(0, (datetime.fromisoformat(end.replace('Z', '+00:00')) -
                       datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())
    except (ValueError, TypeError):
        return None


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {'samples': 0, 'median_seconds': None, 'p95_seconds': None}
    return {'samples': len(values), 'median_seconds': values[len(values) // 2],
            'p95_seconds': values[min(len(values) - 1, int(len(values) * .95))]}


def native_observation(agent, message_times):
    result = {'thread_id': agent['codex_thread_id'], 'coverage': 'unavailable'}
    if not agent['codex_thread_id'] or not agent['codex_home']:
        return result
    try:
        with connect(Path(agent['codex_home']) / 'state_5.sqlite') as db:
            row = db.execute('SELECT rollout_path FROM threads WHERE id=?',
                             (agent['codex_thread_id'],)).fetchone()
        if not row:
            return result
        counts = Counter()
        first_arrival = {}
        latest_config = {}
        # Freeze the byte boundary: ignore a trailing partial JSONL record.
        with open(row['rollout_path'], 'rb') as stream:
            end = os.fstat(stream.fileno()).st_size
            while stream.tell() < end:
                line = stream.readline(end - stream.tell())
                if not line.endswith(b'\n'):
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                payload = event.get('payload', {})
                if event.get('type') == 'turn_context':
                    latest_config = {k: payload.get(k) for k in
                                     ('model', 'effort', 'approval_policy', 'service_tier')}
                    latest_config['sandbox'] = (payload.get('sandbox_policy') or {}).get('type')
                if event.get('type') != 'response_item' or payload.get('role') != 'user':
                    continue
                text = ''.join(c.get('text', '') for c in payload.get('content', [])
                               if isinstance(c, dict))
                match = re.match(r'^\[ACLA (?:message (\d+);|inbox wake-up ([\w-]+)\])', text)
                if not match:
                    continue
                kind = 'legacy_message' if match[1] else 'wake_up'
                key = (kind, match[1] or match[2])
                counts[key] += 1
                first_arrival.setdefault(key, event.get('timestamp'))
        result.update(coverage='observed', runtime=latest_config,
                      transcript_occurrences=sum(counts.values()),
                      unique_envelopes=len(counts),
                      duplicate_occurrences=sum(n - 1 for n in counts.values()),
                      legacy_duplicate_occurrences=sum(n - 1 for (kind, _), n in counts.items()
                                                       if kind == 'legacy_message'),
                      wakeup_duplicate_occurrences=sum(n - 1 for (kind, _), n in counts.items()
                                                       if kind == 'wake_up'),
                      legacy_first_arrival_delay=distribution([
                          seconds(message_times.get(int(key[1])), stamp)
                          for key, stamp in first_arrival.items() if key[0] == 'legacy_message']))
    except (OSError, sqlite3.Error) as exc:
        result['error_type'] = type(exc).__name__
    return result


def codex_token_count(agent):
    """Read Codex's cumulative per-thread token counter without opening rollouts."""
    if not agent.get('codex_thread_id') or not agent.get('codex_home'):
        return None
    try:
        with connect(Path(agent['codex_home']) / 'state_5.sqlite') as db:
            row = db.execute('SELECT tokens_used FROM threads WHERE id=?',
                             (agent['codex_thread_id'],)).fetchone()
        return int(row['tokens_used']) if row and row['tokens_used'] is not None else None
    except (OSError, sqlite3.Error, KeyError, TypeError):
        return None


def claude_token_usage(agent, state):
    """Use the latest cumulative modelUsage record; earlier records are cumulative too."""
    path = Path(state).expanduser().resolve().parent / 'claude-runtime' / (agent['id'] + '.jsonl')
    latest = None
    try:
        with path.open(errors='replace') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') == 'result' and isinstance(event.get('modelUsage'), dict):
                    latest = event['modelUsage']
    except OSError:
        return None
    if latest is None:
        return None
    models = {}
    for model, values in latest.items():
        models[model] = {
            'input_tokens': sum(int(values.get(k) or 0) for k in
                                ('inputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens')),
            'output_tokens': int(values.get('outputTokens') or 0),
            'reported_cost_usd': values.get('costUSD'),
        }
    return {'source': 'Claude Code cumulative modelUsage', 'models': models,
            'input_tokens': sum(v['input_tokens'] for v in models.values()),
            'output_tokens': sum(v['output_tokens'] for v in models.values()),
            'reported_cost_usd': sum(v['reported_cost_usd'] or 0 for v in models.values())}


def token_distribution(values):
    values = sorted(int(v) for v in values if v is not None)
    if not values:
        return {'samples': 0, 'median': None, 'mean': None, 'total': 0}
    return {'samples': len(values), 'median': values[len(values) // 2],
            'mean': round(sum(values) / len(values)), 'total': sum(values)}


def collect(state):
    stamp = utc().isoformat()
    with connect(state) as db:
        db.execute('BEGIN')
        runs = [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY created_at')]
        agents = {r['id']: dict(r) for r in db.execute('SELECT * FROM agents')}
        threads = {r['id']: dict(r) for r in db.execute('SELECT * FROM threads')}
        pairs = [dict(r) for r in db.execute('SELECT * FROM pairs')]
        messages = [dict(r) for r in db.execute('SELECT * FROM messages ORDER BY id')]
        notifications = {r['recipient_id']: dict(r) for r in db.execute('SELECT * FROM inbox_notifications')}
        replies = {r['reply_to'] for r in db.execute('SELECT reply_to FROM idempotency WHERE reply_to IS NOT NULL')}
    times = {m['id']: m['created_at'] for m in messages}
    result = {'schema_version': 1, 'observed_at': stamp, 'runs': [], 'agents': [],
              'limits': ['Snapshots, not an exact event log; brief states between polls can be missed.',
                         'Review/report counts use message prefixes and sender role, not semantic grading.',
                         'Handled does not prove answered; explicit reply-to coverage is reported separately.',
                         'Legacy delivered messages have unknown handling status and are excluded from backlog.',
                         'Transcript duplicates are cumulative visible occurrences, not repeated execution.',
                         'Native transcript format is best effort; missing metrics are unknown, never zero.']}
    for run in runs:
        tids = {t['id'] for t in threads.values() if t['run_id'] == run['id']}
        rows = [m for m in messages if m['thread_id'] in tids]
        eligible = [m for m in rows if not m['legacy'] or m['delivered_at'] is None]
        pending = [m for m in eligible if not m['handled_at']]
        question_prefixes = ('WORKER_QUESTION\n', 'LUNA_QUESTION\n')
        questions = [m for m in rows if m['body'].startswith(question_prefixes)]
        result['runs'].append({
            'run_id': run['id'], 'state': run['state'], 'created_at': run['created_at'],
            'approved_at': run['updated_at'] if run['state'] == 'approved' else None,
            'elapsed_seconds': seconds(run['created_at'], run['updated_at'] if run['state'] == 'approved' else stamp),
            'actors': sum(p['run_id'] == run['id'] for p in pairs),
            'approved_actors': sum(p['run_id'] == run['id'] and p['approved'] for p in pairs),
            'messages': len(rows), 'questions': len(questions),
            'questions_with_explicit_reply': sum(m['id'] in replies for m in questions),
            'unhandled_questions': sum(m in pending for m in questions),
            'review_requests_prefix_count': sum(m['body'].startswith(('MANAGER_REVIEW', 'ASTRA_REVIEW')) for m in rows),
            'worker_report_candidates': sum(agents[m['sender_id']]['kind'] == 'luna' and
                                            not m['body'].startswith(question_prefixes) for m in rows),
            # Preserve the original field for readers of existing local reports.
            'luna_report_candidates': sum(agents[m['sender_id']]['kind'] == 'luna' and
                                          not m['body'].startswith(question_prefixes) for m in rows),
            'legacy_handling_unknown': sum(bool(m['legacy'] and m['delivered_at'] and not m['handled_at']) for m in rows),
            'unhandled': len(pending), 'claimed': sum(bool(m['inbox_token']) for m in pending),
            'oldest_unhandled_seconds': max((seconds(m['created_at'], stamp) or 0 for m in pending), default=None),
            'handled_latency': distribution([seconds(m['created_at'], m['handled_at']) for m in rows if not m['legacy']]),
        })
    agent_tokens = {}
    for aid, agent in agents.items():
        note = notifications.get(aid)
        if agent.get('executor_backend') == 'claude-code':
            usage = claude_token_usage(agent, state)
        else:
            count = codex_token_count(agent)
            usage = ({'source': 'Codex state_5.sqlite tokens_used',
                      'total_tokens': count} if count is not None else None)
        agent_tokens[aid] = usage
        result['agents'].append({'agent_id': aid, 'kind': agent['kind'],
                                'role': 'Manager' if agent['kind'] == 'astra' else 'Worker',
                                'name': agent['name'],
                                'executor_backend': agent.get('executor_backend', 'codex'),
                                'model': agent.get('model'), 'token_usage': usage,
                                'notification_state': note['state'] if note else None,
                                'notification_has_error': bool(note and note['error']),
                                **native_observation(agent, times)})
    # Native Codex threads that are not bound to an ACLA participant form a
    # descriptive local comparison set. They are not matched by task or quality.
    codex_path = next((Path(a['codex_home']) / 'state_5.sqlite'
                       for a in agents.values() if a.get('codex_home')), None)
    acla_codex_ids = {a['codex_thread_id'] for a in agents.values() if a.get('codex_thread_id')}
    controls = []
    if codex_path:
        try:
            with connect(codex_path) as db:
                controls = [dict(r) for r in db.execute(
                    'SELECT id,model_provider,model,source,tokens_used FROM threads WHERE tokens_used>0')]
        except (OSError, sqlite3.Error):
            controls = []
    grouped = {}
    for row in controls:
        if row['id'] in acla_codex_ids:
            continue
        key = (row['model_provider'], row['model'], row['source'])
        grouped.setdefault(key, []).append(row['tokens_used'])
    result['token_usage'] = {
        'acla_agents': [{'kind': a['kind'], 'executor_backend': a.get('executor_backend', 'codex'),
                         'model': a.get('model'), 'token_usage': agent_tokens.get(a['id'])}
                        for a in agents.values()],
        'non_acla_codex_threads_by_model_source': [
            {'model_provider': key[0], 'model': key[1], 'source': key[2],
             **token_distribution(values)}
            for key, values in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), str(kv[0])))]
    }
    role_groups = {}
    for a in agents.values():
        usage = agent_tokens.get(a['id'])
        if not usage:
            continue
        key = (a['kind'], a.get('executor_backend', 'codex'), a.get('model'))
        item = role_groups.setdefault(key, {'samples': 0, 'tokens': [], 'reported_cost_usd': []})
        item['samples'] += 1
        if 'total_tokens' in usage:
            item['tokens'].append(usage['total_tokens'])
        elif 'input_tokens' in usage:
            item['tokens'].append(usage['input_tokens'] + usage['output_tokens'])
            item['reported_cost_usd'].append(usage['reported_cost_usd'])
    result['token_usage']['acla_by_role_backend_model'] = [
        {'kind': key[0], 'role': 'Manager' if key[0] == 'astra' else 'Worker',
         'executor_backend': key[1], 'model': key[2],
         **token_distribution(values['tokens']),
         'reported_cost_usd_total': (round(sum(x for x in values['reported_cost_usd'] if x is not None), 4)
                                     if values['reported_cost_usd'] else None),
         'cost_samples': sum(x is not None for x in values['reported_cost_usd'])}
        for key, values in sorted(role_groups.items(), key=lambda kv: str(kv[0]))]
    result['limits'].extend([
        'Codex token_usage comes from its local cumulative per-thread counter; it has no per-thread USD field here.',
        'Claude modelUsage is cumulative; only the latest record per actor session is used to avoid summing repeated totals.',
        'Claude reported_cost_usd is provider-reported session cost, not a price-list estimate.',
        'Non-ACLA Codex chats are a same-machine descriptive control, not task-matched or quality-scored.'
    ])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path.home() / '.astra-critic-luna-actor/state.sqlite3')
    parser.add_argument('--output', type=Path, default=Path.home() / '.astra-critic-luna-actor/telemetry')
    parser.add_argument('--report', action='store_true', help='Print latest saved snapshot without collecting')
    args = parser.parse_args()
    if args.report:
        print((args.output / 'latest.json').read_text())
        return
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.output / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = collect(args.state)
        encoded = json.dumps(report, separators=(',', ':'))
        with (args.output / (utc().strftime('%Y-%m-%d') + '.jsonl')).open('a') as stream:
            stream.write(encoded + '\n')
        temp = args.output / 'latest.json.tmp'
        temp.write_text(json.dumps(report, indent=2) + '\n')
        temp.replace(args.output / 'latest.json')
        cutoff = (utc() - timedelta(days=30)).date()
        for path in args.output.glob('????-??-??.jsonl'):
            try:
                day = datetime.strptime(path.stem, '%Y-%m-%d').date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
        print(json.dumps({'observed_at': report['observed_at'], 'runs': len(report['runs']),
                          'agents': len(report['agents']), 'report': str(args.output / 'latest.json')}))


if __name__ == '__main__':
    main()
