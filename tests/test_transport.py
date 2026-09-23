from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from acla import delivery, tmux


class QueueDeliveryTests(unittest.TestCase):
    thread = "12345678-1234-5678-1234-567812345678"

    @mock.patch("acla.delivery.subprocess.run")
    @mock.patch("acla.delivery.shutil.which", return_value="/opt/codex/bin/codex")
    def test_uses_queue_argv_and_configured_home(self, _which, run):
        run.return_value.returncode = 0
        delivery.queue_message(self.thread, "hello; $world", codex_home="/private/codex", workspace="/tmp")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/opt/codex/bin/codex", "queue", "--thread", self.thread,
                                  "--message", "hello; $world"])
        self.assertEqual(kwargs["env"]["CODEX_HOME"], "/private/codex")
        self.assertEqual(kwargs["cwd"], "/tmp")
        self.assertNotIn("shell", kwargs)

    def test_rejects_invalid_thread_uuid_before_dispatch(self):
        with mock.patch("acla.delivery.subprocess.run") as run:
            with self.assertRaises(ValueError):
                delivery.queue_message("not-a-uuid", "hello", codex_home="/tmp", workspace="/tmp")
            run.assert_not_called()

    def test_rejects_noncanonical_uuid_and_missing_workspace_before_dispatch(self):
        with mock.patch("acla.delivery.subprocess.run") as run:
            run.return_value.returncode = 0
            with self.assertRaises(ValueError):
                delivery.queue_message("abcdef12-abcd-5678-1234-567812345678".upper(), "hello",
                                       codex_home="/tmp", workspace="/tmp")
            with self.assertRaises(delivery.DeliveryUnavailable):
                delivery.queue_message(self.thread, "hello", codex_home="/tmp",
                                      workspace="/definitely/missing/acla-workspace")
            run.assert_not_called()

    @mock.patch("acla.delivery.shutil.which", return_value=None)
    def test_missing_executable_is_unavailable(self, _which):
        with self.assertRaises(delivery.DeliveryUnavailable):
            delivery.queue_message(self.thread, "hello", codex_home="/tmp", workspace="/tmp")

    @mock.patch("acla.delivery.subprocess.run")
    @mock.patch("acla.delivery.shutil.which", return_value="/opt/codex/bin/codex")
    def test_nonzero_exit_is_uncertain_without_echoing_output(self, _which, run):
        run.return_value.returncode = 2
        run.return_value.stderr = "private payload"
        with self.assertRaises(delivery.DeliveryUncertain) as raised:
            delivery.queue_message(self.thread, "private payload", codex_home="/tmp", workspace="/tmp")
        self.assertNotIn("private payload", str(raised.exception))

    @mock.patch("acla.delivery.subprocess.run", side_effect=TimeoutError("timeout"))
    @mock.patch("acla.delivery.shutil.which", return_value="/opt/codex/bin/codex")
    def test_timeout_is_uncertain(self, _which, _run):
        with self.assertRaises(delivery.DeliveryUncertain):
            delivery.queue_message(self.thread, "hello", codex_home="/tmp", workspace="/tmp")


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TmuxOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.socket = os.path.join(self.tmp.name, "tmux.sock")
        self.session = "acla-test"
        tmux._run("new-session", "-d", "-s", "bootstrap", "-c", self.tmp.name,
                  "--", "sleep 60", socket=self.socket)

    def tearDown(self):
        if os.path.exists(self.socket):
            try:
                tmux._run("kill-server", socket=self.socket)
            except RuntimeError:
                pass
        self.tmp.cleanup()

    def test_launch_records_immutable_ownership_cleans_environment_and_allows_restart(self):
        workspace = self.tmp.name
        env_file = os.path.join(self.tmp.name, "child-env.txt")
        code = ("import os,time,pathlib; pathlib.Path(" + repr(env_file) + ").write_text(" +
                "repr(os.getenv('ACLA_LUNA_ID'))); time.sleep(60)")
        tmux._run("set-environment", "-g", "ACLA_LUNA_ID", "stale-parent-agent", socket=self.socket)
        tmux._run("kill-session", "-t", "=bootstrap", socket=self.socket)
        command = ["python3", "-c", code]
        created = tmux.launch(self.session, workspace, command, agent_id="agent-1", run_id="run-1",
                              role="luna", socket=self.socket,
                              env={"ACLA_AGENT_ID": "spoofed", "CODEX_THREAD_ID": "wrong"})
        self.assertTrue(created)
        time.sleep(1.1)
        saved = tmux.metadata(self.session, self.socket)
        self.assertEqual(saved["ACLA_AGENT_ID"], "agent-1")
        self.assertEqual(saved["ACLA_RUN_ID"], "run-1")
        self.assertTrue(saved["ACLA_PANE_ID"].startswith("%"))
        self.assertTrue(saved["ACLA_PANE_PID"].isdigit())
        self.assertEqual(Path(env_file).read_text(encoding="utf-8"), "None")
        with self.assertRaises(RuntimeError):
            tmux.verify_destination(self.session, "agent-1", "different-run", "luna", self.socket)
        self.assertEqual(tmux.metadata(self.session, self.socket), saved)
        tmux.verify_destination(self.session, "agent-1", "run-1", "luna", self.socket)
        tmux._run("kill-session", "-t", f"={self.session}", socket=self.socket)
        self.assertTrue(tmux.launch(self.session, workspace, command, agent_id="agent-1", run_id="run-1",
                                    role="luna", socket=self.socket))
        time.sleep(1.1)
        tmux.verify_destination(self.session, "agent-1", "run-1", "luna", self.socket)
        tmux.stop_owned(self.session, agent_id="agent-1", run_id="run-1", role="luna", socket=self.socket)
        self.assertFalse(tmux.alive(self.session, self.socket))


if __name__ == "__main__":
    unittest.main()
