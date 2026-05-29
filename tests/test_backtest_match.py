"""Unit tests for calibration.match_trades — no network required."""
import unittest
from dataclasses import dataclass
from typing import Optional


@dataclass
class FakeSimTrade:
    asset: str
    timeframe: str
    entry_time: int
    entry_price: float
    realized_pnl: float
    # Fields the real SimTrade has but we don't need here
    direction: str = "UP"
    size: float = 5.0
    exit_price: float = 0.88
    exit_reason: str = "TARGET_HIT"
    is_reversal: bool = False


def _real(asset, timeframe, entry_time, entry_price, realized_pnl):
    return {
        "asset": asset,
        "timeframe": timeframe,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "realized_pnl": realized_pnl,
    }


class TestMatchTrades(unittest.TestCase):
    def setUp(self):
        from tools.backtest.calibration import match_trades
        self.match_trades = match_trades

    def test_exact_asset_timeframe_match(self):
        real = [_real("BTC", "5m", 1_000_000, 0.50, 2.0)]
        sim  = [FakeSimTrade("BTC", "5m", 1_000_000, 0.52, 1.5)]
        pairs = self.match_trades(real, sim)
        self.assertEqual(len(pairs), 1)
        r, s = pairs[0]
        self.assertEqual(r["asset"], "BTC")
        self.assertEqual(s.asset, "BTC")

    def test_nearest_by_time_selected(self):
        real = [_real("ETH", "15m", 2_000_000, 0.48, -1.0)]
        sim  = [
            FakeSimTrade("ETH", "15m", 1_800_000, 0.49, -0.5),  # 200k away
            FakeSimTrade("ETH", "15m", 2_100_000, 0.47, -0.8),  # 100k away (closer)
        ]
        pairs = self.match_trades(real, sim)
        self.assertEqual(len(pairs), 1)
        _, s = pairs[0]
        self.assertEqual(s.entry_time, 2_100_000)  # closer one selected

    def test_different_asset_not_matched(self):
        real = [_real("BTC", "5m", 1_000_000, 0.50, 2.0)]
        sim  = [FakeSimTrade("ETH", "5m", 1_000_000, 0.50, 2.0)]
        pairs = self.match_trades(real, sim)
        self.assertEqual(len(pairs), 0)

    def test_different_timeframe_not_matched(self):
        real = [_real("SOL", "5m", 1_000_000, 0.50, 1.0)]
        sim  = [FakeSimTrade("SOL", "15m", 1_000_000, 0.50, 1.0)]
        pairs = self.match_trades(real, sim)
        self.assertEqual(len(pairs), 0)

    def test_multiple_real_each_matched(self):
        real = [
            _real("XRP", "5m", 1_000_000, 0.06, 20.0),
            _real("XRP", "5m", 2_000_000, 0.50, -1.0),
        ]
        sim  = [
            FakeSimTrade("XRP", "5m", 1_000_100, 0.06, 18.0),
            FakeSimTrade("XRP", "5m", 2_000_100, 0.50, -0.8),
        ]
        pairs = self.match_trades(real, sim)
        self.assertEqual(len(pairs), 2)

    def test_empty_real_returns_empty(self):
        sim = [FakeSimTrade("BTC", "5m", 1_000_000, 0.50, 1.0)]
        pairs = self.match_trades([], sim)
        self.assertEqual(pairs, [])

    def test_empty_sim_returns_empty(self):
        real = [_real("BTC", "5m", 1_000_000, 0.50, 1.0)]
        pairs = self.match_trades(real, [])
        self.assertEqual(pairs, [])

    def test_wl_agreement_computation(self):
        """Verify that the win/loss signs are correctly readable from returned pairs."""
        real = [
            _real("BTC", "5m", 1_000_000, 0.50, 3.0),   # win
            _real("BTC", "5m", 2_000_000, 0.50, -1.0),  # loss
        ]
        sim  = [
            FakeSimTrade("BTC", "5m", 1_000_000, 0.50, 2.0),   # win — matches
            FakeSimTrade("BTC", "5m", 2_000_000, 0.50, -0.5),  # loss — matches
        ]
        pairs = self.match_trades(real, sim)
        agreements = sum(
            1 for r, s in pairs
            if (float(r["realized_pnl"]) > 0) == (s.realized_pnl > 0)
        )
        self.assertEqual(agreements, 2)  # both signs agree


if __name__ == "__main__":
    unittest.main()
