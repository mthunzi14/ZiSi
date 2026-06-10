"""Mean-reversion displacement study (2026-06-10).

Question: mid-window, when spot has displaced from the window open, how often does
the window CLOSE on the same side — and how does that depend on the volatility
regime (trailing ATR percentile)?

The FV model assumes E[S_T] = S_t (full displacement persistence). If the
empirical continuation rate in low-vol/ranging tape is materially below what
N(d2) implies, the displacement must be shrunk by kappa < 1 before computing d2.

Method: 1m klines from Binance, reconstruct 15m windows; at minute-8 (and 5m
windows at minute-3) take displacement delta = S_t - S_0; record whether
sign(close - open) == sign(delta). Bucket by |delta|/sigma_w (window-scaled
displacement) and by trailing 24h ATR percentile (proxy for regime).
Also compute the empirical P(continue) per bucket vs the model's implied
N(d2) so kappa can be read off directly: kappa = invN(p_emp) / invN(p_model).
"""
import json
import math
import time
import urllib.request
from statistics import NormalDist

ND = NormalDist()
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DAYS = 30
MS_MIN = 60_000


def fetch_1m(symbol, days):
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    out = []
    cur = start
    while cur < end:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval=1m&startTime={cur}&limit=1000")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ks = json.load(r)
        if not ks:
            break
        out.extend(ks)
        cur = ks[-1][0] + MS_MIN
        time.sleep(0.15)
    return out


def study(symbol, win_min, probe_min):
    """win_min: window length (5 or 15); probe_min: decision point inside window."""
    ks = fetch_1m(symbol, DAYS)
    # index by open time
    by_ts = {k[0]: k for k in ks}
    t0 = ks[0][0]
    tN = ks[-1][0]
    # align to window boundaries
    first = ((t0 // (win_min * MS_MIN)) + 1) * (win_min * MS_MIN)
    rows = []
    atr_window = []  # trailing per-window ranges for vol percentile
    t = first
    while t + win_min * MS_MIN <= tN:
        wopen_k = by_ts.get(t)
        probe_k = by_ts.get(t + probe_min * MS_MIN)
        close_k = by_ts.get(t + (win_min - 1) * MS_MIN)
        t_next = t + win_min * MS_MIN
        if wopen_k and probe_k and close_k:
            s0 = float(wopen_k[1])
            st = float(probe_k[1])          # open of the probe minute = spot at probe time
            sc = float(close_k[4])          # window close
            # window range stats from the *prior* 24h of windows
            wret = abs(sc - s0) / s0
            if len(atr_window) >= 20:
                sigma_w = sum(atr_window[-96:]) / len(atr_window[-96:])
                delta = (st - s0) / s0
                if sigma_w > 0 and delta != 0:
                    vol_rank = sum(1 for x in atr_window[-96:] if x < wret) / min(96, len(atr_window))
                    cont = (sc > s0) if delta > 0 else (sc < s0)
                    rows.append((delta / sigma_w, cont, sigma_w))
            atr_window.append(wret)
        t = t_next
    return rows


def summarize(rows, label):
    # bucket by |displacement| / mean window range
    buckets = [(0.1, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.0), (2.0, 99.0)]
    print(f"\n=== {label} (n={len(rows)}) ===")
    print(f"{'disp/range':>12} {'n':>6} {'P(cont)':>8} {'model N(d2)':>11} {'kappa':>6}")
    for lo, hi in buckets:
        sel = [r for r in rows if lo <= abs(r[0]) < hi]
        if len(sel) < 30:
            continue
        p_emp = sum(1 for r in sel if r[1]) / len(sel)
        # model-implied prob at the bucket's mean displacement, half window left:
        # d2 ~= (delta/sigma_w) / sqrt(0.5) using window-range units as sigma proxy
        mean_disp = sum(abs(r[0]) for r in sel) / len(sel)
        d2 = mean_disp / math.sqrt(0.5)
        p_model = ND.cdf(d2 * 0.7978845608)  # E|N| scaling: range units -> sigma units
        try:
            kappa = ND.inv_cdf(max(p_emp, 0.501)) / ND.inv_cdf(max(p_model, 0.501))
        except Exception:
            kappa = float("nan")
        print(f"{lo:>5.1f}-{hi:<5.1f} {len(sel):>6} {p_emp:>8.3f} {p_model:>11.3f} {kappa:>6.2f}")
    p_all = sum(1 for r in rows if r[1]) / len(rows) if rows else 0
    print(f"{'ALL':>12} {len(rows):>6} {p_all:>8.3f}")


if __name__ == "__main__":
    for win, probe in ((15, 8), (5, 3)):
        allrows = []
        for sym in ASSETS:
            rows = study(sym, win, probe)
            summarize(rows, f"{sym} {win}m@min{probe}")
            allrows.extend(rows)
        summarize(allrows, f"COMBINED {win}m@min{probe}")
