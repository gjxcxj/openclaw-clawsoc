#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from soc_store import choose_preferred_endpoint, endpoint_is_suspicious


SCRIPT_DIR = Path(__file__).resolve().parent
CLI_PATH = SCRIPT_DIR / "clawsoc_cli.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(endpoint: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    url = f"{endpoint.rstrip('/')}/clawsoc/health"
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.8) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.2)
    raise AssertionError(f"service did not become healthy: {url} ({last_error})")


class ClawSocSmokeTest(unittest.TestCase):
    def test_endpoint_selection_prefers_real_lan_over_virtual(self) -> None:
        self.assertTrue(endpoint_is_suspicious("http://198.18.0.1:45678"))
        selected = choose_preferred_endpoint("http://198.18.0.1:45678", "http://10.0.0.21:45678")
        self.assertEqual(selected, "http://10.0.0.21:45678")

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="clawsoc-smoke-")
        root = Path(self.tempdir.name)
        self.alpha_root = root / "alpha"
        self.beta_root = root / "beta"
        self.alpha_root.mkdir()
        self.beta_root.mkdir()
        (self.alpha_root / "skills").mkdir()
        (self.beta_root / "skills").mkdir()
        (self.alpha_root / "experience.md").write_text("# alpha experience\nshared lesson\n", encoding="utf-8")
        (self.beta_root / "experience.md").write_text("# beta experience\n", encoding="utf-8")
        (self.alpha_root / "skills" / "demo-skill").mkdir()
        (self.alpha_root / "skills" / "demo-skill" / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: smoke test skill\n---\n\n一个测试技能。\n",
            encoding="utf-8",
        )
        self.alpha_port = free_port()
        self.beta_port = free_port()
        self.alpha_endpoint = f"http://127.0.0.1:{self.alpha_port}"
        self.beta_endpoint = f"http://127.0.0.1:{self.beta_port}"
        self.processes: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.tempdir.cleanup()

    def run_cli(self, workspace_root: Path, *args: str, extra_env: dict[str, str] | None = None) -> str:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        command = ["python3", str(CLI_PATH), "--workspace-root", str(workspace_root), *args]
        result = subprocess.run(command, capture_output=True, text=True, env=env, check=True)
        return result.stdout

    def start_server(
        self,
        workspace_root: Path,
        *,
        name: str,
        bio: str,
        port: int,
        advertise_host: str = "127.0.0.1",
        bind_host: str = "127.0.0.1",
    ) -> subprocess.Popen:
        env = os.environ.copy()
        env.update(
            {
                "CLAWSOC_NAME": name,
                "CLAWSOC_BIO": bio,
                "CLAWSOC_ADVERTISE_HOST": advertise_host,
            }
        )
        command = [
            "python3",
            str(CLI_PATH),
            "--workspace-root",
            str(workspace_root),
            "serve",
            "--host",
            bind_host,
            "--port",
            str(port),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.processes.append(process)
        return process

    def test_pair_chat_upgrade_and_share(self) -> None:
        self.run_cli(
            self.alpha_root,
            "init",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.alpha_port),
            extra_env={"CLAWSOC_NAME": "Alpha", "CLAWSOC_BIO": "alpha claw", "CLAWSOC_ADVERTISE_HOST": "127.0.0.1"},
        )
        self.run_cli(
            self.beta_root,
            "init",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.beta_port),
            extra_env={"CLAWSOC_NAME": "Beta", "CLAWSOC_BIO": "beta claw", "CLAWSOC_ADVERTISE_HOST": "127.0.0.1"},
        )

        self.start_server(self.alpha_root, name="Alpha", bio="alpha claw", port=self.alpha_port)
        self.start_server(self.beta_root, name="Beta", bio="beta claw", port=self.beta_port)
        alpha_health = wait_for_health(self.alpha_endpoint)
        beta_health = wait_for_health(self.beta_endpoint)
        alpha_id = alpha_health["peerId"]
        beta_id = beta_health["peerId"]

        pair_output = self.run_cli(self.alpha_root, "pair", self.beta_endpoint)
        paired = json.loads(pair_output)
        self.assertTrue(paired["ok"])
        self.assertEqual(paired["peer"]["peerId"], beta_id)

        repeat_pair_output = self.run_cli(self.alpha_root, "pair", beta_id)
        repeat_paired = json.loads(repeat_pair_output)
        self.assertTrue(repeat_paired["ok"])
        self.assertEqual(repeat_paired["peer"]["status"], "active")

        alpha_state = json.loads((self.alpha_root / "soc" / "state.json").read_text(encoding="utf-8"))
        beta_state = json.loads((self.beta_root / "soc" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(alpha_state["peers"][beta_id]["status"], "active")
        self.assertEqual(beta_state["peers"][alpha_id]["status"], "active")

        self.run_cli(self.alpha_root, "chat", beta_id, "你好 Beta")
        history_output = self.run_cli(self.beta_root, "history", alpha_id, "--limit", "5")
        self.assertIn("你好 Beta", history_output)

        upgrade_output = self.run_cli(self.alpha_root, "relationship", "upgrade", beta_id, "L1")
        self.assertTrue(json.loads(upgrade_output)["ok"])
        beta_pending = json.loads((self.beta_root / "soc" / "state.json").read_text(encoding="utf-8"))
        pending = beta_pending["pending"]["upgradeRequests"]
        self.assertTrue(any(item["fromPeerId"] == alpha_id and item["targetLevel"] == "L1" for item in pending))

        accept_output = self.run_cli(self.beta_root, "relationship", "accept-upgrade", alpha_id)
        self.assertTrue(json.loads(accept_output)["ok"])

        self.run_cli(self.alpha_root, "share", "skills", beta_id)
        share_dir = self.beta_root / "soc" / "peers" / alpha_id / "shares"
        share_files = list(share_dir.glob("*-skills.json"))
        self.assertTrue(share_files)
        share_payload = json.loads(share_files[-1].read_text(encoding="utf-8"))
        self.assertEqual(share_payload["shareType"], "skills")
        self.assertTrue(share_payload["content"]["skills"])

    def test_bad_advertise_endpoint_falls_back_to_observed_endpoint(self) -> None:
        self.run_cli(
            self.alpha_root,
            "init",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.alpha_port),
            extra_env={"CLAWSOC_NAME": "Alpha", "CLAWSOC_BIO": "alpha claw", "CLAWSOC_ADVERTISE_HOST": "198.18.0.1"},
        )
        self.run_cli(
            self.beta_root,
            "init",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.beta_port),
            extra_env={"CLAWSOC_NAME": "Beta", "CLAWSOC_BIO": "beta claw", "CLAWSOC_ADVERTISE_HOST": "127.0.0.1"},
        )
        self.start_server(
            self.alpha_root,
            name="Alpha",
            bio="alpha claw",
            port=self.alpha_port,
            advertise_host="198.18.0.1",
            bind_host="0.0.0.0",
        )
        self.start_server(self.beta_root, name="Beta", bio="beta claw", port=self.beta_port)
        alpha_health = wait_for_health(self.alpha_endpoint)
        beta_health = wait_for_health(self.beta_endpoint)
        alpha_id = alpha_health["peerId"]
        beta_id = beta_health["peerId"]

        discover_output = self.run_cli(self.beta_root, "discover", "--hosts", "127.0.0.1", "--ports", str(self.alpha_port), "--record")
        discovered = json.loads(discover_output)
        self.assertEqual(discovered["count"], 1)

        pair_output = self.run_cli(self.beta_root, "pair", alpha_id)
        self.assertTrue(json.loads(pair_output)["ok"])

        beta_state = json.loads((self.beta_root / "soc" / "state.json").read_text(encoding="utf-8"))
        alpha_peer = beta_state["peers"][alpha_id]
        self.assertEqual(alpha_peer["endpoint"], self.alpha_endpoint)
        self.assertEqual(alpha_peer["advertisedEndpoint"], "http://198.18.0.1:%s" % self.alpha_port)

        self.run_cli(self.beta_root, "chat", alpha_id, "能收到吗")
        history_output = self.run_cli(self.alpha_root, "history", beta_id, "--limit", "5")
        self.assertIn("能收到吗", history_output)


if __name__ == "__main__":
    unittest.main()
