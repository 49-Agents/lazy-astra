"""Private Worker prompt additions and conservative, durable idle nudges."""
from __future__ import annotations


def settings(config):
    defaults = config.get('defaults', {})
    extra = defaults.get('worker_extra_instructions', '')
    interval = defaults.get('worker_idle_nudge_seconds', 600)
    if not isinstance(extra, str):
        raise ValueError('worker_extra_instructions must be a string')
    if type(interval) is not int or interval < 0:
        raise ValueError('worker_idle_nudge_seconds must be a nonnegative integer (0 disables)')
    return extra.strip(), interval


def extra_prompt(config):
    extra, _ = settings(config)
    return ('\n\nAdditional local owner instructions for this Worker:\n' + extra) if extra else ''


def nudge_prompt(review_enabled):
    review = ('Finish the configured review gate if it is still outstanding. Do not repeat a completed review.'
              if review_enabled else 'Self-review is disabled. Do not launch reviewers or add a review gate.')
    return ('Your previous turn ended without a completion report or blocker question. '
            'Continue only the existing assignment. If implementation is complete, ' + review +
            ' Then send one complete report to Manager using send-reply. If blocked or a planning '
            'decision is missing, use ask-question and wait. Otherwise continue implementation. '
            'Do not send progress updates, acknowledgements, or repeat completed work.')


def confirmed_idle_result(result):
    """Process exit alone is insufficient when native background work may remain."""
    if not isinstance(result, dict) or result.get('is_error') or result.get('stop_reason') != 'end_turn':
        return False
    if result.get('queued_turn_count') != 0:
        return False
    stats = result.get('subagent_stats')
    if not isinstance(stats, dict):
        return False
    spawned, completed = stats.get('spawned'), stats.get('completed')
    # Any uncertain, failed, killed, or still-running child disables nudging.
    return type(spawned) is int and type(completed) is int and spawned >= 0 and spawned == completed


def claim_idle_nudge(store, worker_id, idle_since, clock, interval):
    """Called only by the serial runner with no child process, under its runner lock.

    Persist before dispatch; uncertain launches must not cause automatic repeats.
    At most one nudge per assignment generation (latest Manager message).
    """
    if not interval or idle_since is None or clock - idle_since < interval:
        return False
    store.db.execute('BEGIN IMMEDIATE')
    try:
        actor = store.agent(worker_id)
        pair = store.db.execute('SELECT * FROM pairs WHERE luna_id=?', (worker_id,)).fetchone()
        if (actor['executor_backend'] != 'claude-code' or actor['runtime_error'] or
                not actor['active'] or not pair or pair['approved']):
            store.db.rollback(); return False
        incoming = store.db.execute('SELECT MAX(id) FROM messages WHERE recipient_id=?', (worker_id,)).fetchone()[0] or 0
        outgoing = store.db.execute('SELECT MAX(id) FROM messages WHERE sender_id=?', (worker_id,)).fetchone()[0] or 0
        # Reports and questions both mean waiting for Manager. Never infer them from prose.
        if outgoing > incoming:
            store.db.rollback(); return False
        pending = store.db.execute('SELECT 1 FROM messages WHERE recipient_id=? AND handled_at IS NULL AND legacy=0 LIMIT 1', (worker_id,)).fetchone()
        note = store.db.execute('SELECT state FROM inbox_notifications WHERE recipient_id=?', (worker_id,)).fetchone()
        if pending or (note and note['state'] in ('pending', 'dispatching', 'uncertain', 'claimed')):
            store.db.rollback(); return False
        inserted = store.db.execute('INSERT OR IGNORE INTO worker_idle_nudges(worker_id,assignment_message_id) VALUES(?,?)',
                                    (worker_id, incoming)).rowcount
        store.db.commit()
        return inserted == 1
    except BaseException:
        store.db.rollback()
        raise
