"""Binance kline ingest + caching, plus pure OFI-proxy and ATR transforms."""
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import List

_BINANCE = "https://api.binance.com/api/v3/klines"
_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_base: float

    @classmethod
    def from_binance(cls, row: list) -> "Candle":
        return cls(int(row[0]), float(row[1]), float(row[2]), float(row[3]),
                   float(row[4]), float(row[5]), float(row[9]))


def ofi_proxy(c: Candle) -> float:
    """Kline-derived order-flow imbalance in [-1, +1]: 2*(taker_buy/total) - 1."""
    if c.volume <= 0:
        return 0.0
    ratio = c.taker_buy_base / c.volume
    return max(-1.0, min(1.0, 2.0 * ratio - 1.0))


def atr(candles: List[Candle], period: int = 14) -> float:
    """Average True Range over the last `period` candles (absolute price units)."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def fetch_klines(asset: str, interval: str, start_ms: int, end_ms: int,
                 use_cache: bool = True) -> List[Candle]:
    """Fetch klines for [start_ms, end_ms]; cache the raw rows to tools/backtest/cache."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_CACHE_DIR, f"{asset}_{interval}_{start_ms}_{end_ms}.json")
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            rows = json.load(fh)
        return [Candle.from_binance(r) for r in rows]

    symbol = _SYMBOL[asset]
    rows: list = []
    cursor = start_ms
    while cursor < end_ms:
        url = f"{_BINANCE}?symbol={symbol}&interval={interval}&startTime={cursor}&endTime={end_ms}&limit=1000"
        with urllib.request.urlopen(url, timeout=15) as resp:
            batch = json.loads(resp.read().decode())
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 1
        time.sleep(0.25)  # be polite to the public endpoint
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    return [Candle.from_binance(r) for r in rows]
