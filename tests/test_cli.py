from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acla import cli, delivery
from acla.store import Store


class CliWorkflowTests(unittest.TestCase):
    astra_thread = "12345678-1234-5678-1234-567812345678"
    luna_one_thread = "22345678-1234-5678-1234-567812345678"
    luna_two_thread = "32345678-1234-5678-1234-567812345678"
    run_id = "42345678-1234-5678-1234-567812345678"

    def setUp(self):
        self.backend_default = mock.patch('acla.cli.DEFAULT_BACKEND', 'codex')
        self.backend_default.start()
        self.addCleanup(self.backend_default.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "private" / "state.sqlite3"
        self.state.parent.mkdir(parents=True)
        (self.state.parent / "local.json").write_text(json.dumps({
            "defaults": {"codex_model": "router-model"},
            "models": {"router-model": {"reasoning_effort_supported": False,
                "reasoning_mode": "provider-default", "reasoning_effort": "none",
                "reviewer_model": "router-model"}}
        }))
        self.codex_home = root / "codex-home"
        self.codex_home.mkdir()
        self.workspace_one = root / "actor-one"
        self.workspace_two = root / "actor-two"
        self.workspace_one.mkdir()
        self.workspace_two.mkdir()
        self.handoff = root / "handoff.md"
        self.handoff.write_text("Implement the parser, then report the changed files.")
        self.env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home),
                                                "CODEX_THREAD_ID": self.astra_thread}, clear=False)
        self.env.start()
        self.launcher = mock.patch("acla.cli.launch", return_value=True)
        self.mock_launch = self.launcher.start()
        self.real_start_watcher = cli.start_watcher
        self.watcher = mock.patch("acla.cli.start_watcher", return_value={"session": "watcher-test"})
        self.mock_watcher = self.watcher.start()

    def tearDown(self):
        self.watcher.stop()
        self.launcher.stop()
        self.env.stop()
        self.tmp.cleanup()

    def invoke(self, *argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main(["--state", str(self.state), *map(str, argv)])
        self.assertEqual(code, 0)
        return json.loads(stdout.getvalue())

    def invoke_fails(self, *argv, contains):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            cli.main(["--state", str(self.state), *map(str, argv)])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn(contains, stderr.getvalue())

    def start_actor(self, name="parser", workspace=None, *, run_id=None, handoff=None):
        return self.invoke("run-start", "--run-id", run_id or self.run_id, "--goal", "Review parser",
                           "--workspace", workspace or self.workspace_one, "--luna-name", name,
                           "--handoff-file", handoff or self.handoff, "--interval", "19")

    def store(self):
        return Store(self.state)

    def test_stopped_worker_launcher_update_preserves_identity_and_resumes(self):
        args = ('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                '--workspace', str(self.workspace_one), '--luna-name', 'claude',
                '--handoff-file', str(self.handoff), '--executor-backend', 'claude-code')
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x.rsplit('/', 1)[-1]):
            actor = self.invoke(*args, '--claude-command', 'old-claude')
            store = self.store()
            store.db.execute('UPDATE agents SET claude_initialized=1 WHERE id=?', (actor['luna_id'],))
            store.db.commit()
            before = dict(store.agent(actor['luna_id']))
            self.invoke_fails(*args, '--claude-command', 'claude', contains='different destination command')
            update = ('set-worker-launcher', '--worker-id', actor['luna_id'], '--claude-command', 'claude')
            result = self.invoke(*update)
            after = dict(store.agent(actor['luna_id']))
            self.assertEqual(after.pop('command'), '/usr/bin/claude --model claude-sonnet-5-5')
            before.pop('command')
            self.assertEqual(after, before)
            self.assertTrue(result['session_requirement'])
            self.assertEqual(self.invoke(*update)['command'], result['command'])
            resumed = self.invoke(*args)
            self.assertEqual(resumed['luna_id'], actor['luna_id'])
            self.assertEqual(resumed['claude_session_id'], actor['claude_session_id'])
            store.close()

    def test_launcher_update_rejects_running_wrong_manager_and_missing_binary(self):
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x):
            actor = self.invoke('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                                '--workspace', self.workspace_one, '--luna-name', 'claude',
                                '--handoff-file', self.handoff, '--executor-backend', 'claude-code')
            args = ('set-worker-launcher', '--worker-id', actor['luna_id'], '--claude-command', 'replacement')
            store = self.store()
            before = dict(store.agent(actor['luna_id']))
            with mock.patch('acla.cli.alive', return_value=True):
                self.invoke_fails(*args, contains='Stop the Worker')
            with cli.state_lock(store, 'claude-' + actor['luna_id']):
                self.invoke_fails(*args, contains='owns this state store')
            with mock.patch.dict(os.environ, {'CODEX_THREAD_ID': self.luna_one_thread}):
                self.invoke_fails(*args, contains='not the Manager')
            with mock.patch('acla.cli.shutil.which', return_value=None):
                self.invoke_fails(*args, contains='executable unavailable')
            self.assertEqual(dict(store.agent(actor['luna_id'])), before)
            store.close()

    def test_launcher_update_rejects_codex(self):
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', return_value='/usr/bin/codex'):
            actor = self.start_actor()
            self.invoke_fails('set-worker-launcher', '--worker-id', actor['luna_id'],
                              '--claude-command', 'claude', contains='Claude Code Worker')

    def test_new_actor_defaults_to_claude_sonnet_xhigh(self):
        with mock.patch('acla.cli.DEFAULT_BACKEND', 'claude-code'), mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x):
            result = self.start_actor()
        self.assertEqual(result['executor_backend'], 'claude-code')
        self.assertEqual(result['luna_model'], 'claude-sonnet-5-5')
        self.assertEqual(result['luna_launch_effort'], 'xhigh')
        self.assertTrue(result['reviewLoop'])
        self.assertEqual(result['n_reviewers'], 2)

    def test_claude_defaults_and_identity_are_retained(self):
        args = ('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                '--workspace', str(self.workspace_one), '--luna-name', 'claude',
                '--handoff-file', str(self.handoff))
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x.rsplit('/', 1)[-1]):
            result = self.invoke(*args, '--executor-backend', 'claude-code')
            self.assertEqual(result['luna_model'], 'claude-sonnet-5-5')
            self.assertEqual(result['luna_launch_effort'], 'xhigh')
            self.assertIn('_claude-run', self.mock_launch.call_args.args[2])
            resumed = self.invoke(*args)
            self.assertEqual(resumed['claude_session_id'], result['claude_session_id'])
            self.assertEqual(resumed['executor_backend'], 'claude-code')
            self.invoke_fails(*args, '--executor-backend', 'codex', contains='Backend changes')

    def test_claude_resume_keeps_explicit_old_model_and_effort(self):
        args = ('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                '--workspace', self.workspace_one, '--luna-name', 'claude',
                '--handoff-file', self.handoff, '--executor-backend', 'claude-code')
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x.rsplit('/', 1)[-1]):
            first = self.invoke(*args, '--worker-model', 'claude-opus-5-5', '--worker-effort', 'medium')
            resumed = self.invoke(*args)
        self.assertEqual(resumed['luna_model'], 'claude-opus-5-5')
        self.assertEqual(resumed['luna_launch_effort'], 'medium')
        self.assertEqual(resumed['claude_session_id'], first['claude_session_id'])

    def test_claude_runner_initial_and_resume_turns(self):
        from acla.claude_runner import run
        import argparse
        with mock.patch('acla.cli.alive', return_value=False), mock.patch('acla.cli.shutil.which', side_effect=lambda x: '/usr/bin/' + x):
            actor = self.invoke('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                                '--workspace', self.workspace_one, '--luna-name', 'claude',
                                '--handoff-file', self.handoff, '--executor-backend', 'claude-code')
        store = self.store()
        store.db.execute('UPDATE pairs SET approved=1 WHERE luna_id=?', (actor['luna_id'],))
        store.db.commit()
        store.close()
        for flag in ('--session-id', '--resume'):
            process = mock.Mock()
            process.stdout = io.StringIO(json.dumps({'type':'system', 'subtype':'init',
                'session_id':actor['claude_session_id'], 'permissionMode':'bypassPermissions'}) + '\n' +
                json.dumps({'type':'result', 'is_error':False}) + '\n')
            process.wait.return_value = 0
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_EFFORT_LEVEL': 'low'}), mock.patch('acla.claude_runner.subprocess.Popen', return_value=process) as popen, contextlib.redirect_stdout(io.StringIO()):
                run(argparse.Namespace(state=str(self.state), luna_id=actor['luna_id']))
            argv = popen.call_args.args[0]
            self.assertIn(flag, argv)
            self.assertEqual(argv[argv.index('--effort') + 1], 'xhigh')
            self.assertEqual(popen.call_args.kwargs['env']['CLAUDE_CODE_EFFORT_LEVEL'], 'xhigh')
            self.assertEqual(popen.call_args.kwargs['env']['CLAUDE_CODE_DISABLE_FAST_MODE'], '1')
            self.assertNotIn('CODEX_THREAD_ID', popen.call_args.kwargs['env'])
        store = self.store()
        self.assertEqual(store.agent(actor['luna_id'])['claude_initialized'], 1)
        with mock.patch.dict(os.environ, {'ACLA_LUNA_ID':actor['luna_id'],
                'ACLA_CLAUDE_SESSION_ID':actor['claude_session_id']}):
            self.assertEqual(cli.bound_recipient(store)['id'], actor['luna_id'])
        store.close()

    def test_codex_gpt_still_rejects_medium(self):
        self.invoke_fails('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                         '--workspace', self.workspace_one, '--luna-name', 'gpt',
                         '--handoff-file', self.handoff, '--luna-model', 'gpt-6-luna',
                         '--luna-effort', 'medium', contains='high/xhigh/max for supported Codex models')

    def test_run_start_uses_local_codex_default_and_handoff(self):
        result = self.start_actor()
        self.assertEqual(result["luna_model"], "router-model")
        self.assertEqual(result["state"], "awaiting_actor_binding")
        self.assertEqual(result["watcher"]["session"], "watcher-test")
        self.assertEqual(self.mock_launch.call_args.kwargs["role"], "luna")
        argv = self.mock_launch.call_args.args[2]
        self.assertEqual(argv[:4], ["codex", "--model", "router-model", "--cd"])
        self.assertIn("--sandbox", argv)
        self.assertIn("danger-full-access", argv)
        self.assertIn("--ask-for-approval", argv)
        self.assertIn("never", argv)
        self.assertIn("--add-dir", argv)
        prompt = argv[-1]
        self.assertIn("bind-session --worker-id " + result["luna_id"], prompt)
        self.assertIn("Implement the parser", prompt)
        self.assertNotIn(self.astra_thread, prompt)
        self.assertEqual(self.mock_launch.call_args.kwargs["env"]["CODEX_HOME"], str(self.codex_home.resolve()))
        store = self.store()
        try:
            self.assertEqual(store.agent(result["astra_id"])["codex_thread_id"], self.astra_thread)
            self.assertEqual(store.agent(result["luna_id"])["model"], "router-model")
        finally:
            store.close()

    def test_retry_reuses_actor_and_thread_and_resume_has_no_duplicate_bootstrap(self):
        first = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", first["luna_id"])
        launch_count = self.mock_launch.call_count
        again = self.start_actor()
        self.assertEqual(first["luna_id"], again["luna_id"])
        self.assertEqual(first["thread_id"], again["thread_id"])
        self.assertEqual(self.mock_launch.call_count, launch_count + 1)
        argv = self.mock_launch.call_args.args[2]
        self.assertEqual(argv[:3], ["codex", "resume", self.luna_one_thread])
        self.assertIn("--model", argv)
        self.assertIn("--cd", argv)
        self.assertFalse(any("bind-session" in arg or "Implement the parser" in arg for arg in argv))

    def test_local_model_capabilities_and_effort_rejection(self):
        actor = self.start_actor()
        self.assertFalse(actor['reasoning_effort_supported'])
        self.assertEqual(actor['reasoning_mode'], 'provider-default')
        self.assertEqual(actor['luna_launch_effort'], 'none')
        self.assertIn('model_reasoning_effort="none"', self.mock_launch.call_args.args[2])
        count = self.mock_launch.call_count
        self.invoke_fails('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                          '--workspace', self.workspace_one, '--luna-name', 'parser',
                          '--handoff-file', self.handoff, '--luna-effort', 'high',
                          contains='configured without ACLA reasoning-effort support')
        self.assertEqual(self.mock_launch.call_count, count)

    def test_gpt_resume_preserves_saved_model_and_effort_after_default_change(self):
        actor = self.invoke('run-start', '--run-id', self.run_id, '--goal', 'Review parser',
                            '--workspace', self.workspace_one, '--luna-name', 'parser',
                            '--handoff-file', self.handoff, '--luna-model', 'gpt-6-luna',
                            '--luna-effort', 'xhigh')
        self.assertTrue(actor['reasoning_effort_supported'])
        with mock.patch.dict(os.environ, {'CODEX_THREAD_ID': self.luna_one_thread}):
            self.invoke('bind-session', '--luna-id', actor['luna_id'])
        with mock.patch.dict(os.environ, {'ACLA_LUNA_MODEL': 'other-model'}):
            resumed = self.start_actor()
        self.assertEqual(resumed['luna_model'], 'gpt-6-luna')
        self.assertEqual(resumed['luna_launch_effort'], 'xhigh')
        self.assertIn('model_reasoning_effort="xhigh"', self.mock_launch.call_args.args[2])

    def test_bind_session_reads_actor_native_thread_id(self):
        result = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            bound = self.invoke("bind-session", "--luna-id", result["luna_id"])
        self.assertEqual(bound["codex_thread_id"], self.luna_one_thread)
        store = self.store()
        try:
            self.assertEqual(store.agent(result["luna_id"])["codex_thread_id"], self.luna_one_thread)
        finally:
            store.close()

    def test_send_reply_cannot_impersonate_actor_from_another_codex_task(self):
        result = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", result["luna_id"])
        self.invoke_fails("send-reply", "--luna-id", result["luna_id"], "--body", "done",
                          "--idempotency-key", "reply-1", contains="not the bound Worker")
        store = self.store()
        try:
            self.assertEqual(store.messages(result["thread_id"]), [])
        finally:
            store.close()

    def test_inbox_claims_and_acknowledgements_are_bound_to_native_recipient(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
        self.invoke("send", "--thread-id", actor["thread_id"], "--body", "owner task", "--idempotency-key", "inbox-identity")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("send-reply", "--luna-id", actor["luna_id"], "--body", "actor response", "--idempotency-key", "inbox-status")
        store = self.store()
        try:
            for recipient_id in (actor['astra_id'], actor['luna_id']):
                nid = store.db.execute("SELECT notification_id FROM inbox_notifications WHERE recipient_id=?", (recipient_id,)).fetchone()[0]
                store.notification_dispatching(recipient_id, nid)
                store.notification_result(recipient_id, nid, 'uncertain', 'synthetic ambiguity')
        finally:
            store.close()
        status = self.invoke("status", "--run-id", self.run_id)
        recipients = {row['recipient_id']: row for row in status['recipients']}
        self.assertEqual(recipients[actor['luna_id']]['available_messages'], 1)
        self.assertEqual(recipients[actor['astra_id']]['available_messages'], 1)
        self.assertEqual(recipients[actor['luna_id']]['notification_error'], 'synthetic ambiguity')
        self.assertEqual(recipients[actor['astra_id']]['notification_error'], 'synthetic ambiguity')
        as_astra = self.invoke("inbox", "next")
        self.assertEqual(len(as_astra['messages']), 1)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.astra_thread}):
            after_claim = self.invoke("status", "--run-id", self.run_id)
        self.assertEqual({r['recipient_id']: r for r in after_claim['recipients']}[actor['astra_id']]['claimed_messages'], 1)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            as_luna = self.invoke("inbox", "next")
            self.assertEqual(len(as_luna['messages']), 1)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.astra_thread}):
            self.invoke_fails("inbox", "acknowledge", "--token", as_luna['token'], "--message-id", str(as_luna['messages'][0]['id']), contains="not claimed")

    def test_watcher_poll_rearms_after_partial_claim_expires(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
        for ix in (1, 2):
            self.invoke("send", "--thread-id", actor["thread_id"], "--body", f"work {ix}", "--idempotency-key", f"watcher-expiry-{ix}")
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_count, 1)
        store = self.store()
        try:
            before = store.db.execute("SELECT notification_id FROM inbox_notifications WHERE recipient_id=?", (actor['luna_id'],)).fetchone()[0]
            batch = store.claim_inbox(actor['luna_id'], limit=1)
            store.db.execute("UPDATE messages SET inbox_claimed_at='2000-01-01T00:00:00+00:00' WHERE id=?", (batch['messages'][0]['id'],))
            store.db.commit()
        finally:
            store.close()
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_count, 1)
        store = self.store()
        try:
            current = store.db.execute("SELECT notification_id,state FROM inbox_notifications WHERE recipient_id=?", (actor['luna_id'],)).fetchone()
            self.assertNotEqual(current['notification_id'], before)
            self.assertEqual(current['state'], 'sent')
        finally:
            store.close()

    def test_two_actors_route_enveloped_messages_to_their_bound_threads(self):
        one = self.start_actor("parser", self.workspace_one)
        two = self.start_actor("tests", self.workspace_two)
        for actor, native_id in ((one, self.luna_one_thread), (two, self.luna_two_thread)):
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": native_id}):
                self.invoke("bind-session", "--luna-id", actor["luna_id"])
        self.invoke("send", "--thread-id", one["thread_id"], "--body", "Please add the parser test.",
                    "--idempotency-key", "correction-1")
        self.invoke("send", "--thread-id", two["thread_id"], "--body", "Please add the test fixture.",
                    "--idempotency-key", "correction-2")
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        routed = {call.args[0]: call.args[1] for call in queue.call_args_list}
        self.assertEqual(set(routed), {self.luna_one_thread, self.luna_two_thread})
        for actor, native_id in ((one, self.luna_one_thread),
                                          (two, self.luna_two_thread)):
            envelope = routed[native_id]
            self.assertIn("inbox next", envelope)
            self.assertNotIn("parser test", envelope)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("send-reply", "--luna-id", one["luna_id"], "--body", "Parser test added.",
                        "--idempotency-key", "report-1")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_two_thread}):
            self.invoke("send-reply", "--luna-id", two["luna_id"], "--body", "Fixture added.",
                        "--idempotency-key", "report-2")
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        by_destination = {}
        for call in queue.call_args_list:
            by_destination.setdefault(call.args[0], []).append(call.args[1])
        self.assertEqual(set(by_destination), {self.astra_thread})
        aggregate = "\n".join(by_destination[self.astra_thread])
        self.assertIn("inbox next", aggregate)

    def test_approval_loop_and_uncertain_delivery_require_explicit_resolution(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
            self.invoke("send-reply", "--luna-id", actor["luna_id"], "--body", "Initial implementation report.",
                        "--idempotency-key", "report-initial")
        with mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_args.args[0], self.astra_thread)
        self.assertIn("inbox next", queue.call_args.args[1])
        self.invoke("send", "--thread-id", actor["thread_id"], "--body", "Please fix edge case X.",
                    "--idempotency-key", "correction")
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_args.args[0], self.luna_one_thread)
        self.assertIn("inbox next", queue.call_args.args[1])
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("send-reply", "--luna-id", actor["luna_id"], "--body", "Edge case fixed.",
                        "--idempotency-key", "report-final")
        with mock.patch("acla.cli.queue_message") as queue:
            empty = self.invoke("poll")
        queue.assert_not_called()
        self.assertEqual(empty["delivered"], [])

        self.invoke("approve", "--thread-id", actor["thread_id"], "--body", "Approved.",
                    "--idempotency-key", "approval")
        with mock.patch("acla.cli.queue_message") as queue:
            polled = self.invoke("poll")
        queue.assert_not_called()  # the recipient still has an outstanding wake-up
        status = self.invoke("status", "--run-id", self.run_id)
        self.assertEqual(status["run"]["state"], "approved")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.astra_thread}):
            with mock.patch("acla.cli.queue_message") as queue:
                self.invoke("poll")
        queue.assert_not_called()

    def test_delivery_preflight_unavailable_stays_pending_then_retries_successfully(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
        self.invoke("send", "--thread-id", actor["thread_id"], "--body", "Check delivery retry.",
                    "--idempotency-key", "preflight-retry")
        with mock.patch("acla.cli.verify_destination"), mock.patch(
                "acla.cli.queue_message", side_effect=[delivery.DeliveryUnavailable("codex missing"), None]) as queue:
            first = self.invoke("poll")
            self.assertEqual(first["errors"][0]["state"], "pending")
            store = self.store()
            try:
                message = store.messages(actor["thread_id"])[0]
                self.assertEqual(message["delivery_state"], "pending")
                self.assertIsNone(message["delivered_at"])
            finally:
                store.close()
            second = self.invoke("poll")
        self.assertEqual(queue.call_count, 2)
        self.assertEqual(second["errors"], [])
        self.assertEqual(len(second["delivered"]), 1)
        store = self.store()
        try:
            message = store.messages(actor["thread_id"])[0]
            self.assertEqual(message["delivery_state"], "pending")
            notification = store.db.execute("SELECT state FROM inbox_notifications WHERE recipient_id=?", (actor["luna_id"],)).fetchone()
            self.assertEqual(notification["state"], "sent")
        finally:
            store.close()

    def test_approved_actor_resumes_for_pending_approval_then_stays_stopped(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
        self.invoke("approve", "--thread-id", actor["thread_id"], "--body", "Approved.",
                    "--idempotency-key", "final-approval")
        launch_count = self.mock_launch.call_count

        resumed = self.start_actor()
        self.assertTrue(resumed["launched"])
        self.assertEqual(resumed["state"], "resuming")
        argv = self.mock_launch.call_args.args[2]
        self.assertEqual(argv[:3], ["codex", "resume", self.luna_one_thread])
        self.assertEqual(self.mock_launch.call_count, launch_count + 1)

        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message"):
            delivered = self.invoke("poll")
        self.assertEqual(len(delivered["delivered"]), 1)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            batch = self.invoke("inbox", "next")
        store = self.store()
        try:
            self.assertIsNone(store.db.execute("SELECT 1 FROM inbox_notifications WHERE recipient_id=?", (actor['luna_id'],)).fetchone())
        finally:
            store.close()
        launch_count = self.mock_launch.call_count
        crash_recovery = self.start_actor()
        self.assertTrue(crash_recovery['launched'])
        self.assertEqual(self.mock_launch.call_count, launch_count + 1)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("inbox", "acknowledge", "--token", batch['token'], "--message-id", str(batch['messages'][0]['id']))
        launch_count = self.mock_launch.call_count
        done = self.start_actor()
        self.assertEqual(done["state"], "approved")
        self.assertFalse(done["launched"])
        self.assertEqual(self.mock_launch.call_count, launch_count)

    def test_source_changed_watcher_replaces_only_verified_owned_session(self):
        store = self.store()
        try:
            identity = "watcher-" + hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()[:12]
            session = "acla-" + identity
            with mock.patch("acla.cli.alive", return_value=True), \
                 mock.patch("acla.cli.metadata", return_value={"ACLA_WATCHER_SOURCE": "old-source"}), \
                 mock.patch("acla.cli.verify_destination") as verify, \
                 mock.patch("acla.cli.stop_owned") as stop, \
                 mock.patch("acla.cli.launch", return_value=True) as launch:
                result = self.real_start_watcher(store, 23)
            verify.assert_called_once_with(session, identity, identity, "watcher", cli.DEFAULT_SOCKET)
            stop.assert_called_once_with(session, agent_id=identity, run_id=identity,
                                         role="watcher", socket=cli.DEFAULT_SOCKET)
            args, kwargs = launch.call_args
            self.assertEqual(args[0], session)
            self.assertEqual(kwargs["agent_id"], identity)
            self.assertEqual(kwargs["run_id"], identity)
            self.assertEqual(kwargs["role"], "watcher")
            self.assertTrue(kwargs["env"]["ACLA_WATCHER_SOURCE"])
            self.assertNotEqual(kwargs["env"]["ACLA_WATCHER_SOURCE"], "old-source")
            self.assertEqual(result["session"], session)
            self.assertEqual(result["interval"], 23)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
