import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from acla.store import Store
from acla.cleanup import reap_approved_workers
from test_store import start

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'state.sqlite3')
        self.ids = start(self.store)
        self.clock = datetime(2026, 1, 5, tzinfo=timezone.utc)
        self.store.approve(self.ids['thread_id'], self.ids['astra_id'], 'done', 'approve')
        old = (self.clock - timedelta(days=2)).isoformat()
        self.store.db.execute("UPDATE messages SET created_at=?,handled_at=?,delivery_state='delivered'", (old,old))
        self.store.db.execute("UPDATE inbox_notifications SET state='sent'")
        self.store.db.commit()
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def sweep(self):
        with patch('acla.cleanup.alive', return_value=True), patch('acla.cleanup.stop_owned') as stop:
            result = reap_approved_workers(self.store,self.clock)
            return result,stop.call_count
    def test_approved_old_closed_both_backends_history_preserved(self):
        for backend in ('codex','claude-code'):
            self.store.db.execute('UPDATE agents SET executor_backend=? WHERE id=?',(backend,self.ids['luna_id']))
            self.store.db.commit()
            before=[tuple(r) for r in self.store.db.execute('SELECT * FROM messages')]
            result,calls=self.sweep()
            self.assertEqual(calls,1);self.assertEqual(result['stopped'],[self.ids['luna_id']])
            self.assertEqual(before,[tuple(r) for r in self.store.db.execute('SELECT * FROM messages')])
    def test_recent_activity_for_each_timestamp_prevents_close(self):
        for field in ('created_at','delivered_at','read_at','handled_at'):
            self.store.db.execute(f'UPDATE messages SET {field}=?',((self.clock-timedelta(hours=23)).isoformat(),))
            self.store.db.commit();self.assertEqual(self.sweep()[1],0)
            self.store.db.execute(f'UPDATE messages SET {field}=?',((self.clock-timedelta(days=2)).isoformat(),));self.store.db.commit()
    def test_unapproved_pending_lease_uncertain_prevent_close(self):
        for sql in ("UPDATE pairs SET approved=0", "UPDATE messages SET handled_at=NULL", "UPDATE messages SET inbox_token='lease'", "UPDATE messages SET delivery_state='uncertain'"):
            self.store.db.execute(sql);self.store.db.commit();self.assertEqual(self.sweep()[1],0)
            self.store.db.execute('UPDATE pairs SET approved=1')
            self.store.db.execute("UPDATE messages SET handled_at=created_at,inbox_token=NULL,delivery_state='delivered'");self.store.db.commit()
    def test_ownership_failure_and_missing_session_preserve_history(self):
        with patch('acla.cleanup.alive',return_value=True),patch('acla.cleanup.stop_owned',side_effect=RuntimeError('wrong owner')):
            result=reap_approved_workers(self.store,self.clock)
            self.assertEqual(result['stopped'],[]);self.assertEqual(len(result['errors']),1)
        with patch('acla.cleanup.alive',return_value=False),patch('acla.cleanup.stop_owned') as stop:
            reap_approved_workers(self.store,self.clock);stop.assert_not_called()
    def test_exact_boundary(self):
        self.store.db.execute('UPDATE messages SET created_at=?,handled_at=?',((self.clock-timedelta(days=1)).isoformat(),)*2);self.store.db.commit()
        self.assertEqual(self.sweep()[1],1)

    def test_uncertain_notification_prevents_close(self):
        self.store.db.execute("UPDATE inbox_notifications SET state='uncertain'");self.store.db.commit()
        self.assertEqual(self.sweep()[1],0)
    def test_pending_message_when_approval_arrives_prevents_close(self):
        self.store.db.execute('UPDATE pairs SET approved=0');self.store.db.commit()
        self.store.send(self.ids['thread_id'], self.ids['astra_id'], 'Follow-up')
        self.store.db.execute('UPDATE pairs SET approved=1');self.store.db.commit()
        self.assertEqual(self.sweep()[1],0)
    def test_malformed_timestamp_prevents_close(self):
        self.store.db.execute("UPDATE messages SET handled_at='invalid'");self.store.db.commit()
        result,calls=self.sweep();self.assertEqual(calls,0);self.assertEqual(len(result['errors']),1)
    def test_watcher_once_invokes_cleanup(self):
        from acla.cli import cmd_watch
        import argparse
        with patch('acla.cli.cmd_poll'),patch('acla.cleanup.reap_approved_workers',return_value={'stopped':[],'errors':[]}) as cleanup:
            cmd_watch(argparse.Namespace(state=str(self.store.path),once=True,interval=300))
            cleanup.assert_called_once()
