from __future__ import annotations

import contextlib
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
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "private" / "state.sqlite3"
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

    def test_run_start_uses_native_astra_identity_and_launches_default_luna6_with_handoff(self):
        result = self.start_actor()
        self.assertEqual(result["luna_model"], "gpt-6-luna")
        self.assertEqual(result["state"], "awaiting_actor_binding")
        self.assertEqual(result["watcher"]["session"], "watcher-test")
        self.assertEqual(self.mock_launch.call_args.kwargs["role"], "luna")
        argv = self.mock_launch.call_args.args[2]
        self.assertEqual(argv[:4], ["codex", "--model", "gpt-6-luna", "--cd"])
        self.assertIn("--sandbox", argv)
        self.assertIn("workspace-write", argv)
        self.assertIn("--ask-for-approval", argv)
        self.assertIn("on-request", argv)
        self.assertIn("--add-dir", argv)
        prompt = argv[-1]
        self.assertIn("bind-session --luna-id " + result["luna_id"], prompt)
        self.assertIn("Implement the parser", prompt)
        self.assertNotIn(self.astra_thread, prompt)
        self.assertEqual(self.mock_launch.call_args.kwargs["env"]["CODEX_HOME"], str(self.codex_home.resolve()))
        store = self.store()
        try:
            self.assertEqual(store.agent(result["astra_id"])["codex_thread_id"], self.astra_thread)
            self.assertEqual(store.agent(result["luna_id"])["model"], "gpt-6-luna")
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
                          "--idempotency-key", "reply-1", contains="not the bound Luna")
        store = self.store()
        try:
            self.assertEqual(store.messages(result["thread_id"]), [])
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
        for actor, native_id, content in ((one, self.luna_one_thread, "parser test"),
                                          (two, self.luna_two_thread, "test fixture")):
            envelope = routed[native_id]
            self.assertIn(actor["run_id"], envelope)
            self.assertIn(actor["thread_id"], envelope)
            self.assertIn(actor["astra_id"], envelope)
            self.assertIn(content, envelope)
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
        self.assertIn(one["thread_id"], aggregate)
        self.assertIn(two["thread_id"], aggregate)
        self.assertIn(one["luna_id"], aggregate)
        self.assertIn(two["luna_id"], aggregate)

    def test_approval_loop_and_uncertain_delivery_require_explicit_resolution(self):
        actor = self.start_actor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("bind-session", "--luna-id", actor["luna_id"])
            self.invoke("send-reply", "--luna-id", actor["luna_id"], "--body", "Initial implementation report.",
                        "--idempotency-key", "report-initial")
        with mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_args.args[0], self.astra_thread)
        self.assertIn("Initial implementation report", queue.call_args.args[1])
        self.invoke("send", "--thread-id", actor["thread_id"], "--body", "Please fix edge case X.",
                    "--idempotency-key", "correction")
        with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertEqual(queue.call_args.args[0], self.luna_one_thread)
        self.assertIn("Please fix edge case X", queue.call_args.args[1])
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.luna_one_thread}):
            self.invoke("send-reply", "--luna-id", actor["luna_id"], "--body", "Edge case fixed.",
                        "--idempotency-key", "report-final")
        with mock.patch("acla.cli.queue_message") as queue:
            self.invoke("poll")
        self.assertIn("Edge case fixed", queue.call_args.args[1])

        self.invoke("approve", "--thread-id", actor["thread_id"], "--body", "Approved.",
                    "--idempotency-key", "approval")
        with mock.patch("acla.cli.verify_destination"), mock.patch(
                "acla.cli.queue_message", side_effect=delivery.DeliveryUncertain("ambiguous")):
            polled = self.invoke("poll")
        approval_message = polled["errors"][0]["message_id"]
        self.assertEqual(polled["errors"][0]["state"], "uncertain")
        status = self.invoke("status", "--run-id", self.run_id)
        self.assertEqual(status["run"]["state"], "approved")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.astra_thread}):
            with mock.patch("acla.cli.verify_destination"), mock.patch("acla.cli.queue_message") as queue:
                self.invoke("resolve-delivery", "--message-id", str(approval_message), "--retry")
                self.invoke("poll")
        self.assertEqual(queue.call_args.args[0], self.luna_one_thread)
        self.assertIn("ASTRA_APPROVED", queue.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
