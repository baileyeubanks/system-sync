from __future__ import annotations

import json
import unittest
from pathlib import Path


class CredentialMatrixTests(unittest.TestCase):
    def test_credential_matrix_file_has_required_probe_keys(self) -> None:
        matrix_path = Path("/Users/baileyeubanks/Desktop/ACS_CC_AUTOBOT/V4_REBUILD/05_CREDENTIAL_MATRIX.json")
        self.assertTrue(matrix_path.exists(), "credential matrix json was not generated")

        payload = json.loads(matrix_path.read_text())
        probes = payload.get("probes", {})
        self.assertIn("google_oauth", probes)
        self.assertIn("google_dwd", probes)
        self.assertIn("wix_contacts", probes)
        self.assertIn("elevenlabs_stt", probes)
        self.assertIn("x_usage", probes)

    def test_credential_matrix_has_service_rows(self) -> None:
        matrix_path = Path("/Users/baileyeubanks/Desktop/ACS_CC_AUTOBOT/V4_REBUILD/05_CREDENTIAL_MATRIX.json")
        payload = json.loads(matrix_path.read_text())
        rows = payload.get("rows", [])
        services = {row.get("service") for row in rows}
        self.assertTrue({"google", "wix"}.issubset(services))


if __name__ == "__main__":
    unittest.main()

