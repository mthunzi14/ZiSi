import unittest
import json
import os
from unittest.mock import patch, mock_open
from core.engine.regime_filter import get_regime_mode, time_gate_open, apply_regime

class TestRegimeFilter(unittest.TestCase):
    def test_time_gate_open(self):
        self.assertTrue(time_gate_open())

    def test_apply_regime(self):
        # REBUILD: momentum is FADED in MEAN_REVERSION, FOLLOWED in TREND.
        self.assertEqual(apply_regime("UP", "TREND"), "UP")
        self.assertEqual(apply_regime("DOWN", "TREND"), "DOWN")
        self.assertEqual(apply_regime("UP", "MEAN_REVERSION"), "DOWN")
        self.assertEqual(apply_regime("DOWN", "MEAN_REVERSION"), "UP")
        # Fair-value / reversal signals (is_momentum=False) are never flipped:
        self.assertEqual(apply_regime("UP", "MEAN_REVERSION", is_momentum=False), "UP")
        self.assertEqual(apply_regime("DOWN", "TREND", is_momentum=False), "DOWN")

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_regime_mode_range(self, mock_file, mock_exists):
        mock_exists.return_value = True
        mock_file.return_value.read.return_value = json.dumps({"regime": "RANGE"})
        
        mode = get_regime_mode()
        self.assertEqual(mode, "MEAN_REVERSION")

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_regime_mode_normal(self, mock_file, mock_exists):
        mock_exists.return_value = True
        mock_file.return_value.read.return_value = json.dumps({"regime": "NORMAL"})
        
        mode = get_regime_mode()
        self.assertEqual(mode, "MEAN_REVERSION")

    @patch("os.path.exists")
    def test_get_regime_mode_no_file(self, mock_exists):
        mock_exists.return_value = False
        mode = get_regime_mode()
        self.assertEqual(mode, "TREND")

if __name__ == "__main__":
    unittest.main()
