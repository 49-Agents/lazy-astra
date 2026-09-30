import tempfile
import unittest
from pathlib import Path

from acla.store import Store
from acla.worker_policy import settings, extra_prompt, nudge_prompt, confirmed_idle_result, claim_idle_nudge
from test_store import start


class WorkerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.ids = start(self.store, executor_backend='claude-code', handoff='Implement fixture')
        self.worker = self.ids['luna_id']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def claim(self, **kw):
        args = dict(idle_since=10, clock=610, interval=600)
        args.update(kw)
        return claim_idle_nudge(self.store, self.worker, **args)

    def test_extra_prompt_and_validation(self):
        self.assertEqual(extra_prompt({}), '')
        self.assertIn('focused tests', extra_prompt({'defaults': {'worker_extra_instructions': 'focused tests'}}))
        self.assertEqual(settings({})[1], 600)
        for config in ({'worker_extra_instructions': 42}, {'worker_idle_nudge_seconds': True}, {'worker_idle_nudge_seconds': -1}):
            with self.assertRaises(ValueError): settings({'defaults': config})

    def test_requires_confirmed_end_and_completed_children(self):
        event = dict(stop_reason='end_turn', queued_turn_count=0, subagent_stats={'spawned': 2, 'completed': 2})
        self.assertTrue(confirmed_idle_result(event))
        for patch in ({'is_error': True}, {'queued_turn_count': 1}, {'subagent_stats': None},
                      {'subagent_stats': {'spawned': 2, 'completed': 1}}, {'stop_reason': 'max_tokens'}):
            self.assertFalse(confirmed_idle_result(event | patch))
        self.assertFalse(confirmed_idle_result({}))

    def test_delay_disable_and_restart_deduplication(self):
        self.assertFalse(self.claim(clock=609))
        self.assertFalse(self.claim(idle_since=None))
        self.assertFalse(self.claim(interval=0))
        self.assertTrue(self.claim())
        self.store.close();self.store = Store(self.path)
        self.assertFalse(self.claim(clock=1200))

    def test_completion_and_question_suppress_nudge(self):
        for body in ('Completion report', 'WORKER_QUESTION\nNeed a decision'):
            with self.subTest(body=body):
                self.store.send(self.ids['thread_id'], self.worker, body)
                self.assertFalse(self.claim())

    def test_pending_input_and_approval_and_error_suppress_nudge(self):
        self.store.send(self.ids['thread_id'], self.ids['astra_id'], 'Feedback')
        self.assertFalse(self.claim())
        self.store.db.execute('UPDATE messages SET handled_at=created_at')
        self.store.db.execute('DELETE FROM inbox_notifications')
        self.store.db.execute('UPDATE pairs SET approved=1')
        self.store.db.commit()
        self.assertFalse(self.claim())
        self.store.db.execute('UPDATE pairs SET approved=0')
        self.store.db.execute("UPDATE agents SET runtime_error='failed' WHERE id=?", (self.worker,))
        self.store.db.commit()
        self.assertFalse(self.claim())

    def test_new_feedback_allows_one_new_nudge_after_handling(self):
        self.assertTrue(self.claim())
        self.store.send(self.ids['thread_id'], self.ids['astra_id'], 'Next revision')
        self.store.db.execute('UPDATE messages SET handled_at=created_at')
        self.store.db.execute('DELETE FROM inbox_notifications')
        self.store.db.commit()
        self.assertTrue(self.claim())
        self.assertFalse(self.claim())

    def test_uncertain_notification_and_codex_never_nudged(self):
        self.store.send(self.ids['thread_id'], self.ids['astra_id'], 'Revise')
        self.store.db.execute('UPDATE messages SET handled_at=created_at')
        self.store.db.execute("UPDATE inbox_notifications SET state='uncertain'")
        self.store.db.commit()
        self.assertFalse(self.claim())
        self.store.db.execute('DELETE FROM inbox_notifications')
        self.store.db.execute("UPDATE agents SET executor_backend='codex' WHERE id=?", (self.worker,))
        self.store.db.commit()
        self.assertFalse(self.claim())

    def test_disabled_review_nudge_does_not_enable_review(self):
        self.assertIn('Self-review is disabled', nudge_prompt(False))
        self.assertIn('Do not repeat a completed review', nudge_prompt(True))
