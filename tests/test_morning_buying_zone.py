#!/usr/bin/env python3
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from morning_buying_zone import normalize_zone, send_telegram_morning_update, get_last_morning_date, set_last_morning_date

class TestMorningBuyingZone(unittest.TestCase):

    def test_normalize_zone(self):
        self.assertEqual(normalize_zone("🟢🟢 ADD AGGRESSIVELY"), "🟢🟢 ADD AGGRESSIVELY")
        self.assertEqual(normalize_zone("ADD AGGRESSIVELY"), "🟢🟢 ADD AGGRESSIVELY")
        self.assertEqual(normalize_zone("🟢 ACCUMULATE"), "🟢 ACCUMULATE")
        self.assertEqual(normalize_zone("ACCUMULATE"), "🟢 ACCUMULATE")
        self.assertEqual(normalize_zone("🟠 SMALL BUY"), "🟠 SMALL BUY")
        self.assertEqual(normalize_zone("🟡 SMALL BUY"), "🟠 SMALL BUY")
        self.assertEqual(normalize_zone("SMALL BUY"), "🟠 SMALL BUY")
        self.assertEqual(normalize_zone("🔎 INVESTIGATE WHY"), "🔎 INVESTIGATE WHY")
        self.assertEqual(normalize_zone("INVESTIGATE"), "🔎 INVESTIGATE WHY")
        self.assertEqual(normalize_zone("❌ WAIT"), "❌ WAIT")
        self.assertEqual(normalize_zone("WAIT"), "❌ WAIT")
        self.assertIsNone(normalize_zone("UNKNOWN"))

    @patch("morning_buying_zone.send_telegram")
    def test_send_telegram_morning_update(self, mock_send):
        mock_send.return_value = True
        records = [
            {"Symbol": "RELIANCE", "CMP": 2500, "Total Score": 85, "Final Action": "BUY", "Buying Zone": "🟢🟢 ADD AGGRESSIVELY", "Buy/Sell Price Range": "₹2400-2600"},
            {"Symbol": "TCS", "CMP": 3500, "Total Score": 78, "Final Action": "ACCUMULATE", "Buying Zone": "🟢 ACCUMULATE", "Buy/Sell Price Range": "₹3400-3600"},
            {"Symbol": "INFY", "CMP": 1800, "Total Score": 65, "Final Action": "HOLD", "Buying Zone": "🟠 SMALL BUY", "Buy/Sell Price Range": ""},
            {"Symbol": "WIPRO", "CMP": 450, "Total Score": 40, "Final Action": "WAIT", "Buying Zone": "❌ WAIT", "Buy/Sell Price Range": ""},
        ]
        success = send_telegram_morning_update(records, 24500.0, 0.45)
        self.assertTrue(success)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("MORNING BUYING ZONE UPDATE", msg)
        self.assertIn("NIFTY 50:", msg)
        self.assertIn("RELIANCE", msg)
        self.assertIn("TCS", msg)
        self.assertIn("INFY", msg)
        self.assertIn("WAIT / EXPENSIVE:", msg)

    def test_idempotency_state_helpers(self):
        mock_ws = MagicMock()
        mock_ws.acell.return_value.value = "2026-09-03"
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_ws

        # Get date
        d = get_last_morning_date(mock_sh)
        self.assertEqual(d, "2026-09-03")

        # Set date
        set_last_morning_date(mock_sh, "2026-09-04")
        mock_ws.update_acell.assert_called_once_with("B1", "2026-09-04")

if __name__ == "__main__":
    unittest.main()
