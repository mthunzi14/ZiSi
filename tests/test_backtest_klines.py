import unittest
from tools.backtest.klines import ofi_proxy, atr, Candle


def _c(o, h, l, c, vol, taker_buy):
    # Binance kline row: [openTime,o,h,l,c,vol,closeTime,quoteVol,trades,takerBuyBase,...]
    return Candle.from_binance([0, o, h, l, c, vol, 0, 0, 0, taker_buy, 0, 0])


class TestKlines(unittest.TestCase):
    def test_ofi_proxy_bounds_and_sign(self):
        all_buy = _c(10, 10, 10, 10, 100.0, 100.0)   # taker_buy == total -> +1
        all_sell = _c(10, 10, 10, 10, 100.0, 0.0)    # taker_buy == 0    -> -1
        balanced = _c(10, 10, 10, 10, 100.0, 50.0)   # half -> 0
        self.assertAlmostEqual(ofi_proxy(all_buy), 1.0, places=4)
        self.assertAlmostEqual(ofi_proxy(all_sell), -1.0, places=4)
        self.assertAlmostEqual(ofi_proxy(balanced), 0.0, places=4)

    def test_ofi_proxy_zero_volume(self):
        self.assertEqual(ofi_proxy(_c(10, 10, 10, 10, 0.0, 0.0)), 0.0)

    def test_atr_constant_series_is_zero(self):
        candles = [_c(10, 10, 10, 10, 1, 0.5) for _ in range(20)]
        self.assertAlmostEqual(atr(candles, period=14), 0.0, places=6)

    def test_atr_positive_for_ranged_series(self):
        candles = [_c(10, 11, 9, 10, 1, 0.5) for _ in range(20)]  # range 2 each bar
        self.assertGreater(atr(candles, period=14), 0.0)
