import tempfile
import threading
import unittest
import uuid
import os
from pathlib import Path

from acla.store import Store


def start(store, **kw):
    args = dict(goal="review", run_id="run-1", astra_id=None, astra_name="Astra",
                astra_workspace=None, astra_session=None, astra_socket=None, astra_command=None,
                luna_name="Luna", luna_id=None, luna_workspace="/tmp", luna_session="",
                luna_socket="", luna_command=None)
    args.update(kw)
    return store.start_run(**args)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_retry_reuses_identity_and_rejects_changed_payload(self):
        first = start(self.store, astra_thread_id=str(uuid.uuid4()), codex_home="/codex", handoff="handoff")
        second = start(self.store, astra_id=first["astra_id"], luna_id=first["luna_id"],
                       astra_thread_id=self.store.agent(first["astra_id"])["codex_thread_id"],
                       codex_home="/codex", handoff="handoff")
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "different handoff"):
            start(self.store, astra_id=first["astra_id"], luna_id=first["luna_id"],
                  astra_thread_id=self.store.agent(first["astra_id"])["codex_thread_id"],
                  codex_home="/codex", handoff="changed")

    def test_new_luna_persists_codex_home_model_and_handoff(self):
        result = start(self.store, codex_home="/codex-home", luna_model="gpt-test", handoff="review brief")
        luna = self.store.agent(result["luna_id"])
        pair = self.store.db.execute("SELECT handoff FROM pairs WHERE luna_id=?", (result["luna_id"],)).fetchone()
        self.assertEqual(luna["codex_home"], "/codex-home")
        self.assertEqual(luna["model"], "gpt-test")
        self.assertEqual(pair["handoff"], "review brief")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path, self.path.resolve())

    def test_concurrent_same_run_is_idempotent(self):
        results, errors = [], []
        barrier = threading.Barrier(2)
        def work():
            db = Store(self.path)
            try:
                barrier.wait()
                results.append(start(db))
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()
        threads = [threading.Thread(target=work) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors)
        self.assertEqual(results[0], results[1])

    def test_codex_thread_binding_validates_role_home_and_replacement(self):
        ids = start(self.store)
        thread_id = str(uuid.uuid4())
        self.store.bind_codex_thread(ids["luna_id"], thread_id, "/home/codex")
        self.store.bind_codex_thread(ids["luna_id"], thread_id, "/home/codex")
        with self.assertRaisesRegex(ValueError, "different Codex thread"):
            self.store.bind_codex_thread(ids["luna_id"], str(uuid.uuid4()), "/home/codex")
        with self.assertRaisesRegex(ValueError, "Codex home"):
            self.store.bind_codex_thread(ids["luna_id"], thread_id, "/other")
        with self.assertRaisesRegex(ValueError, "only Luna"):
            self.store.bind_codex_thread(ids["astra_id"], thread_id, "/home/codex")

    def test_approval_closes_actor_and_run_only_after_all_actors(self):
        one = start(self.store)
        two = start(self.store, run_id="run-1", luna_name="Luna 2")
        msg = self.store.approve(one["thread_id"], one["astra_id"], "looks good", "approval-1")
        self.assertTrue(msg["body"].startswith("ASTRA_APPROVED\n"))
        self.assertEqual(msg["id"], self.store.approve(one["thread_id"], one["astra_id"], "looks good", "approval-1")["id"])
        with self.assertRaisesRegex(ValueError, "approved"):
            self.store.send(one["thread_id"], one["astra_id"], "late", "late")
        self.assertEqual(self.store.status("run-1")["run"]["state"], "working")
        with self.assertRaisesRegex(ValueError, "original idempotency key"):
            self.store.approve(one["thread_id"], one["astra_id"], "looks good", "approval-new-key")
        self.store.approve(two["thread_id"], two["astra_id"], "approved", "approval-2")
        self.assertEqual(self.store.status("run-1")["run"]["state"], "approved")
        retry = start(self.store, astra_id=one["astra_id"], luna_id=one["luna_id"])
        self.assertEqual(retry["luna_id"], one["luna_id"])
        with self.assertRaisesRegex(ValueError, "approved run"):
            start(self.store, run_id="run-1", luna_name="Late Luna")

    def test_dispatch_claim_retries_only_before_dispatch_and_explicitly_resolves_uncertain(self):
        ids = start(self.store)
        message = self.store.send(ids["thread_id"], ids["astra_id"], "hello", "hello-1")
        claimed = self.store.claim_pending("worker", limit=1)
        self.assertEqual([r["id"] for r in claimed], [message["id"]])
        self.store.release_claim(message["id"], "worker", "queue unavailable")
        claimed = self.store.claim_pending("worker", limit=1)
        self.store.mark_dispatching(message["id"], "worker")
        self.store.reset_undispatched(message["id"], "worker", "queue executable unavailable")
        self.assertEqual(self.store.claim_pending("worker", limit=1)[0]["id"], message["id"])
        self.store.mark_dispatching(message["id"], "worker")
        self.store.mark_uncertain(message["id"], "worker", "queue acknowledgement lost")
        self.assertEqual(self.store.claim_pending("other"), [])
        self.store.resolve_delivery(message["id"], retry=True)
        self.assertEqual(self.store.claim_pending("worker", limit=1)[0]["id"], message["id"])

    def test_concurrent_claimers_never_receive_same_message(self):
        ids = start(self.store)
        message = self.store.send(ids["thread_id"], ids["astra_id"], "hello", "hello-2")
        barrier = threading.Barrier(2)
        claims = []
        def work(worker):
            db = Store(self.path)
            barrier.wait()
            claims.append(db.claim_pending(worker, limit=1))
            db.close()
        threads = [threading.Thread(target=work, args=(name,)) for name in ("worker-a", "worker-b")]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(len(claim) for claim in claims), 1)
        self.assertEqual(next(claim[0]["id"] for claim in claims if claim), message["id"])

    def test_additive_migration_preserves_legacy_rows(self):
        self.store.close()
        self.path.unlink()
        import sqlite3
        db = sqlite3.connect(self.path)
        db.executescript("""
            CREATE TABLE runs(id TEXT PRIMARY KEY, goal TEXT NOT NULL, state TEXT NOT NULL,
                astra_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE agents(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                workspace TEXT, tmux_session TEXT, tmux_socket TEXT, command TEXT,
                bootstrap_sent INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE pairs(luna_id TEXT PRIMARY KEY, astra_id TEXT NOT NULL, run_id TEXT NOT NULL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, thread_id TEXT, sender_id TEXT, recipient_id TEXT,
                body TEXT, created_at TEXT, delivered_at TEXT, read_at TEXT,
                delivery_claim TEXT, delivery_claimed_at TEXT);
            INSERT INTO runs VALUES('old-run','old goal','working',NULL,'t','t');
            INSERT INTO messages(id,delivered_at) VALUES(1,'delivered timestamp');
        """)
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute("SELECT goal FROM runs WHERE id='old-run'").fetchone()["goal"], "old goal")
        self.assertIn("codex_thread_id", {row["name"] for row in self.store.db.execute("PRAGMA table_info(agents)")})
        self.assertEqual(self.store.db.execute("SELECT delivery_state FROM messages WHERE id=1").fetchone()["delivery_state"], "delivered")
        self.assertEqual(self.store.db.execute("SELECT legacy FROM messages WHERE id=1").fetchone()["legacy"], 1)

    def test_claim_ack_reply_and_legacy_targeting(self):
        ids = start(self.store)
        first = self.store.send(ids['thread_id'], ids['astra_id'], 'question', 'q')
        self.store.send(ids['thread_id'], ids['astra_id'], 'another', 'q2')
        a = self.store.claim_inbox(ids['luna_id'], limit=2)
        self.assertEqual([m['id'] for m in a['messages']], [first['id'], first['id'] + 1])
        self.assertEqual(self.store.claim_inbox(ids['luna_id'])['messages'], [])
        self.store.send(ids['thread_id'], ids['luna_id'], 'answer', 'r', reply_to=first['id'])
        self.assertIsNotNone(self.store.messages(ids['thread_id'])[0]['handled_at'])
        with self.assertRaises(ValueError):
            self.store.acknowledge(ids['luna_id'], a['token'], [first['id']])
        self.store.acknowledge(ids['luna_id'], a['token'], [first['id'] + 1])
        self.assertEqual(self.store.claim_inbox(ids['luna_id'])['messages'], [])

    def test_reply_validation_and_idempotency_conflict_do_not_consume(self):
        one = start(self.store)
        two = start(self.store, luna_name='Luna 2')
        msg = self.store.send(one['thread_id'], one['astra_id'], 'question', 'question')
        with self.assertRaises(ValueError):
            self.store.send(two['thread_id'], two['astra_id'], 'bad reply', 'bad', reply_to=msg['id'])
        self.assertIsNone(self.store.db.execute('SELECT handled_at FROM messages WHERE id=?', (msg['id'],)).fetchone()[0])
        self.store.send(one['thread_id'], one['luna_id'], 'reply', 'reply-key', reply_to=msg['id'])
        with self.assertRaisesRegex(ValueError, 'idempotency conflict'):
            self.store.send(one['thread_id'], one['luna_id'], 'different', 'reply-key', reply_to=None)

    def test_expired_inbox_claim_restores_one_wakeup(self):
        ids = start(self.store)
        self.store.send(ids['thread_id'], ids['astra_id'], 'work', 'lease-work')
        batch = self.store.claim_inbox(ids['luna_id'])
        self.assertEqual(self.store.notification_batch(), [])
        self.assertEqual(self.store.recover_inbox_claims(lease_seconds=-1), 1)
        self.assertEqual(len(self.store.notification_batch()), 1)
        recovered = self.store.claim_inbox(ids['luna_id'])
        self.assertEqual([m['id'] for m in recovered['messages']], [batch['messages'][0]['id']])

    def test_recipient_wakeup_coalesces_and_reply_to_removes_stale_wakeup(self):
        ids = start(self.store)
        first = self.store.send(ids['thread_id'], ids['astra_id'], 'one', 'coalesce-1')
        notification = self.store.db.execute('SELECT notification_id FROM inbox_notifications WHERE recipient_id=?', (ids['luna_id'],)).fetchone()[0]
        second = self.store.send(ids['thread_id'], ids['astra_id'], 'two', 'coalesce-2')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox_notifications WHERE recipient_id=?', (ids['luna_id'],)).fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT notification_id FROM inbox_notifications WHERE recipient_id=?', (ids['luna_id'],)).fetchone()[0], notification)
        batch = self.store.claim_inbox(ids['luna_id'])
        self.assertEqual([m['id'] for m in batch['messages']], [first['id'], second['id']])

        other = start(self.store, run_id='other-run', luna_name='Other Luna')
        target = self.store.send(other['thread_id'], other['astra_id'], 'question', 'proactive')
        self.store.send(other['thread_id'], other['luna_id'], 'answered before poll', 'proactive-reply', reply_to=target['id'])
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM inbox_notifications WHERE recipient_id=?', (other['luna_id'],)).fetchone())

    def test_uncertain_wakeup_is_quarantined_but_explicit_inbox_still_works(self):
        ids = start(self.store)
        self.store.send(ids['thread_id'], ids['astra_id'], 'work', 'uncertain-work')
        self.store.notification_dispatching(ids['luna_id'])
        self.store.notification_result(ids['luna_id'], 'uncertain', 'ambiguous')
        self.assertEqual(self.store.notification_batch(), [])
        self.assertEqual(len(self.store.claim_inbox(ids['luna_id'])['messages']), 1)
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM inbox_notifications WHERE recipient_id=?', (ids['luna_id'],)).fetchone())


if __name__ == "__main__":
    unittest.main()
