import unittest
from core.engine.trade_priority_queue import PriorityQueue, FeedbackTracker, TradePriority, _get_priority

class TestTradePriorityQueue(unittest.TestCase):
    def test_get_priority(self):
        critical_trade = {
            "signal": {"signal_type": "TYPE_A_HIGH"},
            "market": {"market_category": "CRYPTO"}
        }
        self.assertEqual(_get_priority(critical_trade), TradePriority.CRITICAL)

        fair_trade = {
            "signal": {"signal_type": "TYPE_B_HIGH"},
            "market": {"market_category": "MACRO"}
        }
        self.assertEqual(_get_priority(fair_trade), TradePriority.FAIR)

        low_trade = {
            "signal": {"signal_type": "TYPE_B_LOW"}
        }
        self.assertEqual(_get_priority(low_trade), TradePriority.LOW)

    def test_prioritize(self):
        trades = [
            {"id": "t_low", "signal": {"signal_type": "TYPE_B_LOW"}},
            {"id": "t_critical", "signal": {"signal_type": "TYPE_A_HIGH"}, "market": {"market_category": "CRYPTO"}},
            {"id": "t_fair", "signal": {"signal_type": "TYPE_B_HIGH"}, "market": {"market_category": "MACRO"}}
        ]
        
        queue = PriorityQueue()
        sorted_trades = queue.prioritize(trades)
        
        self.assertEqual(len(sorted_trades), 3)
        self.assertEqual(sorted_trades[0]["id"], "t_critical")
        self.assertEqual(sorted_trades[1]["id"], "t_fair")
        self.assertEqual(sorted_trades[2]["id"], "t_low")

    def test_feedback_tracker(self):
        tracker = FeedbackTracker()
        tracker.record("TYPE_A_HIGH", "CRYPTO", 0.9, "WIN")
        tracker.record("TYPE_A_HIGH", "CRYPTO", 0.8, "LOSS")
        tracker.record("TYPE_A_HIGH", "CRYPTO", 0.95, "WIN")
        
        self.assertAlmostEqual(tracker.win_rate("TYPE_A_HIGH", "CRYPTO"), 2.0 / 3.0)
        
        summary = tracker.summary()
        self.assertEqual(summary["TYPE_A_HIGH|CRYPTO"]["wins"], 2)
        self.assertEqual(summary["TYPE_A_HIGH|CRYPTO"]["losses"], 1)

if __name__ == "__main__":
    unittest.main()
