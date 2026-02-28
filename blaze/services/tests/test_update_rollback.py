from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class UpdateRollbackTests(unittest.TestCase):
    def test_broken_update_triggers_rollback(self) -> None:
        runtime_root = Path("/Users/baileyeubanks/Desktop/ACS_CC_AUTOBOT/Blaze-V4")
        script = runtime_root / "ops" / "scripts" / "openclaw_update_with_rollback.sh"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_root = Path(tmp) / "snapshots"
            snapshot_root.mkdir(parents=True, exist_ok=True)

            fake_openclaw = Path(tmp) / "openclaw"
            fake_openclaw.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    if [[ "${1:-}" == "--version" ]]; then
                      echo "1.2.3"
                      exit 0
                    fi
                    if [[ "${1:-}" == "update" && "${2:-}" == "status" ]]; then
                      echo '{"channel":"stable","current":"1.2.3"}'
                      exit 0
                    fi
                    if [[ "${1:-}" == "update" ]]; then
                      if [[ "$*" == *"--tag 1.2.3"* ]]; then
                        echo '{"ok":true,"rollback":true}'
                        exit 0
                      fi
                      echo '{"ok":false}' >&2
                      exit 1
                    fi
                    if [[ "${1:-}" == "health" ]]; then
                      echo '{"status":"ok"}'
                      exit 0
                    fi
                    echo '{}'
                    """
                )
            )
            fake_openclaw.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = "{tmp}:{rest}".format(tmp=tmp, rest=env["PATH"])
            env["SKIP_RUNTIME_SMOKE"] = "true"
            env["SNAPSHOT_ROOT"] = str(snapshot_root)

            result = subprocess.run(
                ["bash", str(script)],
                env=env,
                cwd=str(runtime_root),
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0, "update script should fail when update fails")
            after_paths = sorted(snapshot_root.glob("*"), key=lambda p: p.stat().st_mtime)
            self.assertTrue(after_paths, "snapshot directory should exist")
            newest = after_paths[-1]
            rollback_file = newest / "openclaw_rollback_result.json"
            self.assertTrue(rollback_file.exists(), "rollback result should be recorded")


if __name__ == "__main__":
    unittest.main()
