from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.system_deployed import load_deployed_info


class SystemDeployedTests(unittest.TestCase):
    def test_missing_stamp_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = load_deployed_info(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "missing_deploy_stamp")

    def test_parses_key_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "DEPLOYED_FROM_GITHUB.txt").write_text(
                "deployed_at=2026-02-18T00:00:00Z\n"
                "git_branch=main\n"
                "git_commit=abc123\n"
                "git_remote=git@github.com:example/repo.git\n"
            )
            result = load_deployed_info(root)
            self.assertTrue(result["ok"])
            deployed = result["deployed"]
            self.assertEqual(deployed["git_branch"], "main")
            self.assertEqual(deployed["git_commit"], "abc123")


if __name__ == "__main__":
    unittest.main()

