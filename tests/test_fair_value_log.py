import json
import os
import tempfile
import unittest
from infrastructure.state.fair_value_log import log_fair_value_entry


class TestFairValueLog(unittest.TestCase):
    def test_appends_jsonl_row(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fv.jsonl")
            log_fair_value_entry(
                {"asset": "BTC", "timeframe": "15m", "direction": "UP",
                 "fp_up": 0.62, "quote": 0.50, "edge": 0.12, "archetype": "moderate",
                 "entry_ts": 1780000000.0}, path=path)
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["direction"], "UP")
            self.assertAlmostEqual(rows[0]["quote"], 0.50)

    def test_two_entries_append(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fv.jsonl")
            for _ in range(2):
                log_fair_value_entry({"asset": "ETH", "quote": 0.4}, path=path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(sum(1 for l in fh if l.strip()), 2)
