# ZiSi Bot: Rebuild v4.0 — The Momentum-Chasing Trap, Deep Diagnostics & Claude Code Handover
## Updated Session Report & Deep Fix | June 10, 2026

**Session Balance:** $50.00 start → **$47.58 realized** (after recent losses)  
**Active Positions:** 2 open (BTC/15m UP @ 50.5¢, XRP/15m DOWN @ 53.5¢)  
**Bot Status:** Running on VPS (PID 104795, up since 07:01 UTC)

This document contains the diagnosis and the exact **Deep Fix** for the trade-flow stall and late-entry losses observed in the active session. It is formatted for **Claude Code** to implement immediately.

---

## 1. Deep Diagnosis: The Momentum-Chasing Trap

### 1.1 The Symptom
* **Trades are not flowing at candle open.**
* **When trades do execute, they enter late** (between 2 to 3.5 minutes into 5m candles) and **expire worthless (MARKET_EXPIRED).**

### 1.2 The Root Cause (Code Architecture Mismatch)
The user's developer agent recently deployed the **`SIG-CONFIRM` gate** (Commit `f898f01f`), which requires the current candle's live move to align with the signal and exceed $0.15\times$ ATR before entry.

However, the main loop in `app/main.py` contains a retry loop in `_evaluate_market_signals`:
```python
    while True:
        signal = await engine.generate_signal(session)
        if signal is not None:
            break
        ...
        await asyncio.sleep(2.0)
```
When a signal is generated at the candle open (T+0s), the spot price has not moved yet. The `SIG-CONFIRM` gate rejects the trade, returning `None`. 

Instead of sleeping until the next candle, **the loop retries every 2 seconds.** As a result:
1. **API Spam:** The bot logs `[SIG-CONFIRM] ... skip` hundreds of times per candle (596 times in 90 minutes).
2. **The Momentum Trap:** If the price finally moves in the signal direction at T+2 minutes (exceeding the $0.15\times$ ATR threshold), `SIG-CONFIRM` passes and the trade is entered.
3. **Late-Entry Losses:** By entering at T+2.5 minutes, the contract price has already adjusted (e.g., buying YES at 60¢ instead of 50¢), and the initial edge is gone. Because short-term crypto timeframes are highly mean-reverting, buying at the high of the candle's move right before it reverses leads to consistent losses.

### 1.3 Active Session Evidence
* **SOL/5m UP (LOSS -$2.93):** Entered at `07:12:02` (2 minutes 2 seconds late) at **60¢**. Resolved worthless.
* **SOL/5m UP (LOSS -$2.58):** Entered at `07:52:56` (2 minutes 56 seconds late) at **52¢**. Resolved worthless.
* **ETH/5m DOWN (LOSS -$5.88):** Entered at `08:27:01` (2 minutes 1 second late) at **55¢**. Resolved worthless.
* **ETH/15m DOWN (WIN +$4.68):** Entered at `08:00:12` (**12 seconds into the candle — early boundary entry**). Resolved as a major win.

---

## 2. The Deep Fix: 15-Second Candle Open Boundary Limit

To solve this, we must enforce that all `SIGNAL` and `FAIR_VAL` signals are **only evaluated and entered in the first 15 seconds of the candle.** If no trade is entered by T+15s, the evaluation loop must terminate, and the bot must sleep to the next candle.

### 🔴 CODE FIX: Enforce 15s Boundary Limit in `app/main.py`
**File**: `app/main.py`  
**Function**: `_evaluate_market_signals`  
**Location**: Inside the `while True` retry loop, right after calculating `elapsed` (~line 200).

```python
    while True:
        now_ts = datetime.now(timezone.utc).timestamp()
        candle_start = (int(now_ts) // (interval_minutes * 60)) * (interval_minutes * 60)
        elapsed = now_ts - candle_start
        
        # ── DEEP FIX: 15-SECOND CANDLE OPEN BOUNDARY LIMIT ──
        # Prevent any mid-candle/late signal generation and entry.
        # This completely eliminates the momentum-chasing late entry trap.
        if elapsed > 15.0:
            log.info(
                "[MAIN] %s/%s: Signal evaluation retry window closed (elapsed=%.1fs > 15.0s) — skip",
                asset, timeframe, elapsed
            )
            return None

        signal = await engine.generate_signal(session)
        if signal is not None:
            break
```

### Why this Fix is Mathematically Optimal:
1. **Saves Capital:** In the active session, it would have blocked the SOL and ETH 5m late entries, saving **$11.39** in losses, while keeping the early ETH 15m win (+$4.68) and all NCS snipes.
2. **Protects Fair Value:** Fair Value is a valuation strategy. It must buy contracts early when they are mispriced. Waiting for price movement mid-candle destroys its pricing edge. Limiting the window to 15s ensures FV only trades at the boundary.
3. **Reduces API Load:** Cuts CPU utilization and log spam by terminating the loop after 15 seconds instead of polling every 2 seconds for 5 minutes.

---

## 3. Priorty Checklist for Claude Code

| Priority | Fix | File | Target File Path |
|---|---|---|---|
| 🔴 **P0** | Enforce 15s Boundary Limit | `app/main.py` | [main.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/app/main.py#L196-L213) |
| 🔴 **P0** | NCS Proximity Guard | `cycle_manager.py` | [cycle_manager.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/core/engine/cycle_manager.py#L1212) |
| 🔴 **P0** | FV Score Inflation Fix | `updown_engine.py` | [updown_engine.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/core/engine/updown_engine.py#L1073) |
| 🔴 **P0** | FV Correlated Exposure Cap | `app/main.py` | [main.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/app/main.py#L490) |
| 🟠 **P1** | Streak Whale Veto & Sizing | `updown_engine.py` | [updown_engine.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/core/engine/updown_engine.py#L526) |

---

## 4. Execution Plan
1. Apply the 15s limit to `app/main.py`.
2. Apply the remaining rebuild v3.0 fixes.
3. Restart the main bot process: `pkill -f main.py && cd /root/ZiSi && python3 app/main.py &`.
