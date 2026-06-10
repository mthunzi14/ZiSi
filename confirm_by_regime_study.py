"""SIG-CONFIRM threshold conditioned on volatility regime (2026-06-10).

Question: after a window's early move clears the 0.15x confirm threshold, does
continuation hold up in LOW-vol (mean-reverting) tape, or is the 61-69%
unconditional number carried by high-vol periods?

Method: 1m klines, 30 days, BTC/ETH/SOL/XRP. For each 5m window (probe min 2)
and 15m window (probe min 2 and 8): trailing vol = mean |close-open|/open of the
prior 12 windows, ranked against the prior 288 windows (trailing percentile, no
lookahead). Continuation = sign(close-open) == sign(probe displacement).
Report P(cont | disp ratio bucket) per trailing-vol tercile.
"""
import json
import time
import urllib.request

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
        time.sleep(0.12)
    return {k[0]: k for k in out}


def run(win_min, probe_min, data_by_asset):
    rows = []  # (vol_tercile, disp_ratio, cont)
    for sym, by_ts in data_by_asset.items():
        ts_all = sorted(by_ts.keys())
        first = ((ts_all[0] // (win_min * MS_MIN)) + 1) * (win_min * MS_MIN)
        last = ts_all[-1]
        wrets = []
        t = first
        while t + win_min * MS_MIN <= last:
            o_k = by_ts.get(t)
            p_k = by_ts.get(t + probe_min * MS_MIN)
            c_k = by_ts.get(t + (win_min - 1) * MS_MIN)
            if o_k and p_k and c_k:
                s0, st, sc = float(o_k[1]), float(p_k[1]), float(c_k[4])
                wret = abs(sc - s0) / s0
                if len(wrets) >= 300:
                    recent = wrets[-12:]
                    trail = sum(recent) / len(recent)
                    hist = wrets[-300:-12]
                    pct = sum(1 for x in hist if x < trail) / len(hist)
                    base = wrets[-96:]
                    sigma_w = sum(base) / len(base)
                    delta = (st - s0) / s0
                    if sigma_w > 0 and delta != 0:
                        ratio = abs(delta) / sigma_w
                        cont = (sc > s0) if delta > 0 else (sc < s0)
                        terc = 0 if pct < 0.333 else (1 if pct < 0.667 else 2)
                        rows.append((terc, ratio, cont))
                wrets.append(wret)
            t += win_min * MS_MIN
    print(f"\n=== {win_min}m windows, probe minute {probe_min} (n={len(rows)}) ===")
    print(f"{'vol regime':>10} {'disp bucket':>12} {'n':>6} {'P(cont)':>8}")
    names = {0: "LOW", 1: "MID", 2: "HIGH"}
    for terc in (0, 1, 2):
        for lo, hi in ((0.15, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 99.0)):
            sel = [r for r in rows if r[0] == terc and lo <= r[1] < hi]
            if len(sel) < 30:
                continue
            p = sum(1 for r in sel if r[2]) / len(sel)
            print(f"{names[terc]:>10} {f'{lo}-{hi}':>12} {len(sel):>6} {p:>8.3f}")
        allsel = [r for r in rows if r[0] == terc and r[1] >= 0.15]
        if allsel:
            p = sum(1 for r in sel if r[2]) / len(sel) if sel else 0
            pa = sum(1 for r in allsel if r[2]) / len(allsel)
            print(f"{names[terc]:>10} {'ALL>=0.15':>12} {len(allsel):>6} {pa:>8.3f}")


if __name__ == "__main__":
    data = {}
    for sym in ASSETS:
        print(f"fetching {sym}...")
        data[sym] = fetch_1m(sym, DAYS)
    for win, probe in ((5, 2), (15, 2), (15, 8)):
        run(win, probe, data)
