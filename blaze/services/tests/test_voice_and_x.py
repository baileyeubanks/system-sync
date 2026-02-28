from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.connectors.elevenlabs_connector import ElevenLabsConfig, ElevenLabsConnector
from api.connectors.x_connector import XConfig, XConnector
from api.db import Database
from api.intent_router import route_intent


class VoiceAndXTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_voice_end_to_end_fallback_pipeline(self) -> None:
        voice = ElevenLabsConnector(
            ElevenLabsConfig(
                api_key="",
                default_voice_id="",
                stt_model_id="scribe_v1",
                tts_model_id="eleven_turbo_v2_5",
            )
        )
        stt = voice.transcribe(None, text_hint="find Rocco Richards contact")
        self.assertTrue(stt["ok"])
        intent = route_intent(stt["text"])
        self.assertEqual(intent["intent"], "contact_lookup")
        tts = voice.speak("Found Rocco Richards in CC", voice_id=None)
        self.assertTrue(tts["ok"])
        self.assertIn(tts["mode"], {"mock", "live"})

    def test_x_budget_enforcement_warning_and_disable(self) -> None:
        x_api = XConnector(
            self.db,
            XConfig(enabled=True, bearer_token="", cap_usd=25.0, warning_ratio=0.8),
        )
        warning = x_api.record_usage(20.0)
        self.assertEqual(warning["status"], "warning")
        self.assertTrue(warning["ratio"] >= 0.8)

        capped = x_api.record_usage(5.0)
        self.assertEqual(capped["status"], "cap_reached_auto_disabled")
        self.assertFalse(capped["enabled"])

    def test_x_kill_switch_env_policy(self) -> None:
        x_api = XConnector(
            self.db,
            XConfig(enabled=False, bearer_token="token", cap_usd=25.0, warning_ratio=0.8),
        )
        usage = x_api.get_usage()
        self.assertEqual(usage["status"], "disabled_by_policy")
        self.assertFalse(usage["enabled"])


if __name__ == "__main__":
    unittest.main()

