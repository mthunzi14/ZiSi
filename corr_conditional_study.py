"""Cross-asset correlation conditional on lead move size (2026-06-10).

User thesis: "when BTC does a LARGE move, all the other coins move with it."
Implementation today shadows ANY entered lead. This measures P(alt window closes
in BTC's direction | BTC's mid-window displacement, in window-range units) so the
shadow spawn threshold can be set from data.
"""
import json
import time
import urllib.request

ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
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


def run(win_min, probe_min, btc, alts):
    ts_all = sorted(btc.keys())
    first = ((ts_all[0] // (win_min * MS_MIN)) + 1) * (win_min * MS_MIN)
    last = ts_all[-1]
    rows = []  # (btc_disp_ratio, {alt: follows})
    ranges = []
    t = first
    while t + win_min * MS_MIN <= last:
        bo_k = btc.get(t)
        bp_k = btc.get(t + probe_min * MS_MIN)
        bc_k = btc.get(t + (win_min - 1) * MS_MIN)
        if bo_k and bp_k and bc_k:
            b0, bt_, bc = float(bo_k[1]), float(bp_k[1]), float(bc_k[4])
            bret = abs(bc - b0) / b0
            if len(ranges) >= 20:
                sigma_w = sum(ranges[-96:]) / len(ranges[-96:])
                delta = (bt_ - b0) / b0
                if sigma_w > 0 and delta != 0:
                    ratio = abs(delta) / sigma_w
                    bdir = 1 if delta > 0 else -1
                    follows = {}
                    for name, alt in alts.items():
                        ao_k = alt.get(t)
                        ac_k = alt.get(t + (win_min - 1) * MS_MIN)
                        if ao_k and ac_k:
                            a0, ac = float(ao_k[1]), float(ac_k[4])
                            adir = 1 if ac >= a0 else -1
                            follows[name] = (adir == bdir)
                    if follows:
                        rows.append((ratio, follows))
            ranges.append(bret)
        t += win_min * MS_MIN
    # summarize
    buckets = [(0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 99.0)]
    print(f"\n=== {win_min}m windows, BTC displacement at minute {probe_min} (n={len(rows)}) ===")
    hdr = f"{'btc disp/rng':>13} {'n':>6}" + "".join(f" {a.replace('USDT',''):>6}" for a in ALTS) + f" {'ALL-ALTS':>8}"
    print(hdr)
    for lo, hi in buckets:
        sel = [r for r in rows if lo <= r[0] < hi]
        if len(sel) < 30:
            continue
        cells = []
        tot_f = tot_n = 0
        for a in ALTS:
            f = [r[1][a] for r in sel if a in r[1]]
            tot_f += sum(f); tot_n += len(f)
            cells.append(f"{(sum(f)/len(f) if f else 0):>6.3f}")
        print(f"{lo:>5.2f}-{hi:<6.2f} {len(sel):>6}" + " ".join([""]) + " ".join(cells) + f" {tot_f/max(tot_n,1):>8.3f}")


if __name__ == "__main__":
    print("fetching BTC...")
    btc = fetch_1m("BTCUSDT", DAYS)
    alts = {}
    for a in ALTS:
        print(f"fetching {a}...")
        alts[a] = fetch_1m(a, DAYS)
    for win, probe in ((15, 2), (15, 8), (5, 1), (5, 2)):
        run(win, probe, btc, alts)
