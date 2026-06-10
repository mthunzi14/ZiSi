# ZiSi — Forward Scaling & Regime-Conditional Analysis
## Deeper Strategic Modeling & Alpha Roadmap for Rebuild v3.0

This document contains the secondary strategic layer for the ZiSi Bot rebuild. It provides the mathematical and operational guidelines for scaling the bot from $50 to $1,000+, models performance across different market regimes, outlines risk-of-ruin vectors, and details the future machine learning (ANN) and sentiment-based expansions.

---

## 1. Per-Strategy Forward Scaling Analysis

### 1.1 Kalshi Liquidity Constraints & Slippage Limits
Prediction markets like Kalshi operate in a distinct microstructure compared to centralized crypto exchanges (like Binance or Coinbase). The order books for short-duration binary contracts (5m/15m) are thin. Sizing decisions must respect the available liquidity at each contract tier.

```
Contract Pricing  | Typical Bid/Ask Depth (Contracts) | Max Dollar Size before Slippage > 1¢
─────────────────────────────────────────────────────────────────────────────────────────────
85¢ - 98¢ (NCS)   | 50 - 250 contracts                | $50 - $200 (Slippage wipes out edge)
40¢ - 60¢ (SIG/FV)| 200 - 1,000 contracts             | $100 - $400 (Deeper liquidity pool)
10¢ - 30¢ (Cheap) | 100 - 500 contracts               | $20 - $100 (High spread friction)
```

- **NCS (Close-Snipe)**: Because these trades enter at near-certainty prices (e.g. 95¢), a 1¢ increase in entry price (from 95¢ to 96¢) reduces the payout ratio from 1.052x to 1.041x—a **20% reduction in net profit**. Buying more than $150–200 worth of contracts sweeps the book, driving entry prices to 97¢+ and rendering the strategy statistically unprofitable. **A hard ceiling of $200 per trade is enforced.**
- **SIGNAL / FAIR-VAL**: These trades enter at mid-prices (30¢–70¢) where liquidity is thicker. Slippage of 1¢ at a 50¢ entry price only reduces the payout ratio from 2.0x to 1.96x (a 4% profit reduction). These scale more easily up to $400 per trade.
- **LAT-ARB**: Extremely time-sensitive. The edge depends on stale quotes (2–5s latency). High-size orders will trigger the market makers' protection limits, causing partial fills. **A hard ceiling of $30 per trade is enforced.**

### 1.2 Portfolio Allocation & Sizing Brackets
To scale safely from a $50 start to $1,000+, ZiSi must transition from static sizing to dynamic bracket-based risk limits. The portfolio allocation is weighted by each strategy's historical profit factor and risk-of-ruin profile.

#### Strategy Weights (Optimal Portfolio)
- **SIGNAL (SIG)**: **40%** (Highest alpha, profit factor 3.21, resilient to chop)
- **NCS (Close-Snipe)**: **30%** (High win-rate but high tail-risk; capped exposure)
- **FAIR-VAL (FV)**: **15%** (Mean-reverting, useful to capture ranges)
- **LAT-ARB**: **10%** (Low-risk, speed-constrained, high Sharpe ratio)
- **REVERSAL-STREAK**: **5%** (Selective, low-volume, trend-exhaustion)

#### Account Balance Sizing Brackets

| Account Balance | NCS Max Size (30%) | SIG Max Size (40%) | FV Max Size (15%) | LAT-ARB Max Size (10%) | Streak Max Size (5%) |
|---|---|---|---|---|---|
| **$50 (Start)** | $15.00 (75 contracts) | $10.00 (20 contracts) | $7.50 (15 contracts) | $5.00 (10 contracts) | $2.50 (5 contracts) |
| **$100** | $30.00 | $20.00 | $15.00 | $10.00 | $5.00 |
| **$250** | $75.00 | $50.00 | $37.50 | $25.00 | $12.50 |
| **$500** | $150.00 | $100.00 | $75.00 | $30.00 **(Hard Cap)** | $25.00 |
| **$1,000+** | $200.00 **(Hard Cap)** | $200.00 | $150.00 | $30.00 **(Hard Cap)** | $50.00 |

---

## 2. Regime-Conditional Performance Projections

ZiSi’s performance is highly sensitive to the broader market regime. The 69 trades analyzed in our baseline were executed during a choppy, post-drop recovery. The table below projects how the **fixed** bot will perform across the three major market regimes.

```
REGIME TYPE            | Strategy Impact                          | Blended WR | Est. Net/Session
─────────────────────────────────────────────────────────────────────────────────────────────
1. TRENDING            | - NCS: Outperformance (98% WR)           |   88.5%    |    +$65.00
   (High momentum,     | - SIG: Capture large momentum waves      |            |  (High compounding)
   low whipsaw)        | - FV: Vetoes filter bad contra-trades    |            |
                       | - LAT-ARB: Moderate volume               |            |
─────────────────────────────────────────────────────────────────────────────────────────────
2. VOLATILE_CHAOS      | - NCS: High risk (90% WR, news spikes)   |   78.2%    |    +$8.00
   (High ATR, sudden   | - SIG: Chopped by deep whipsaws          |            | (Flat/conservation)
   1-min reversals)    | - FV: Heavy correlated veto triggers     |            |
                       | - LAT-ARB: High volume, high slippage    |            |
─────────────────────────────────────────────────────────────────────────────────────────────
3. COMPRESSION / CHOP  | - NCS: Proximity guard vetoes ~30% trades|   92.0%    |    +$35.00
   (Low ATR, range-    | - SIG: Flat (low breakout conversion)    |            |   (Low volatility)
   bound, mean-revert) | - FV: Outperformance (85% WR)            |            |
                       | - LAT-ARB: Zero volume                   |            |
```

### Regime-Specific Adjustments for Claude Code to Implement:
1. **In TRENDING**: Increase SIG sizing by 20% (scale Kelly fraction from 0.08 to 0.10). Loosen the SIG entry score threshold to 0.70 to capture early-stage moves.
2. **In VOLATILE_CHAOS**: Reduce all sizing limits by 50% (preservation mode). Force a maximum of 1 open position per candle timeframe across all strategies.
3. **In COMPRESSION**: Reduce NCS sizing by 30% (spreads are thin, spot-strike distance is narrow). Increase FV sizing to full Kelly.

---

## 3. Risk-of-Ruin Stress Test (Worst-Case Scenarios)

Even with all fixes applied, specific market conditions can cause severe drawdowns. We stress-test two maximum-drawdown scenarios to define hard limits.

### 3.1 Scenario A: The News-Spike Cascade (NCS Tail-Event)
- **Conditions**: High-impact news event (Fed rate decision, major regulatory announcement) occurs in the final 30 seconds of a candle window.
- **The Event**: Prices across BTC, ETH, and SOL spike 1.5% instantly.
- **The Failure**: The proximity guard passes because spot was far from strike when evaluated at T-45s. The contracts were purchased at 96¢ ($200 size each, total exposure $600 on a $1,000 balance). The sudden spike pushes all three contracts into the loss zone (resolving 0¢).
- **The Damage**: -$576 net loss (a 57.6% account drawdown in a single candle).
- **Required Mitigation (Claude Code)**:
  - **Cross-Asset NCS Cap**: Limit maximum concurrent NCS contracts to **1 asset per candle timeframe**. If BTC Close-Snipe is active, do not enter ETH or SOL Close-Snipe. This limits maximum single-candle tail damage to $192 (at $200 max size).

### 3.2 Scenario B: Whipsaw Loop (Multiple Correlation Losses)
- **Conditions**: Highly correlated market chop (e.g. BTC bounces up and down 0.3% every 5 minutes).
- **The Event**: The bot enters 2 SIG long positions (BTC/ETH) and 2 FV short positions (SOL/XRP) in overlapping windows. The market whipsaws, hitting the stops on the longs, then reverses and hits the stops on the shorts.
- **The Damage**: 4 simultaneous losses of ~$10 each = -$40 (a 40% loss on a $100 starting balance).
- **Required Mitigation (Claude Code)**:
  - **Global Concurrent Position Cap**: Limit the total open positions across *all* active strategies to a maximum of **3 at any given millisecond**. This prevents portfolio-wide liquidation loops.

---

## 4. Alpha Development Roadmap (New Strategies & Models)

To progress from heuristic rule-based systems to a modern, robust quantitative trading agent, Claude Code should implement the following three components.

### 4.1 Lightweight ANN Score Generator
Instead of manually tuning indicators (RSI, OFI, OBI), we propose training a simple Multi-Layer Perceptron (MLP) to predict contract expiration probability.

#### Feature Matrix ($X$):
1. `spot_distance_pct`: `(pyth_price - candle_open) / candle_open`
2. `rsi_14`: Rolling Relative Strength Index
3. `ofi_ema_5m`: Order Flow Imbalance (Binance)
4. `clob_obi`: contract-level Order Book Imbalance (Polymarket)
5. `atr_ratio`: `current_atr / baseline_atr`
6. `time_remaining_sec`: Seconds to candle close

#### Target ($y$):
- `1` if contract resolves YES, `0` if NO.

#### Model Architecture (scikit-learn / PyTorch):
A lightweight 2-layer MLP (`6 -> 8 -> 4 -> 1`) with Sigmoid output. Running this locally on the VPS takes <1ms for inference.

#### Python Implementation Blueprint for Claude Code:
```python
# core/ml/ann_predictor.py
import numpy as np
import torch
import torch.nn as nn
import logging

log = logging.getLogger("zisi.ml")

class ScoreMLP(nn.Module):
    def __init__(self):
        super(ScoreMLP, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(6, 8),
            nn.ReLU(),
            nn.Linear(8, 4),
            nn.ReLU(),
            nn.Linear(4, 1),
            nn.Sigmoid()
        )
        
    def forward(self, x):
        return self.net(x)

class ANNEngine:
    def __init__(self, model_path="core/ml/weights/zisi_mlp.pt"):
        self.model = ScoreMLP()
        self.is_loaded = False
        try:
            self.model.load_state_dict(torch.load(model_path))
            self.model.eval()
            self.is_loaded = True
            log.info("ANN weights loaded successfully from %s", model_path)
        except Exception as e:
            log.error("Failed to load ANN weights: %s. Falling back to heuristic scoring.", e)

    def predict_probability(self, spot_dist, rsi, ofi, obi, atr_ratio, time_rem) -> float:
        if not self.is_loaded:
            return 0.50  # Neutral fallback
            
        # Standardize inputs using pre-calculated training means/stdevs
        # (Example values shown for illustration)
        features = np.array([
            (spot_dist - 0.0002) / 0.0012,
            (rsi - 50.0) / 15.0,
            (ofi - 0.0) / 0.45,
            (obi - 0.0) / 0.35,
            (atr_ratio - 1.0) / 0.30,
            (time_rem - 30.0) / 15.0
        ], dtype=np.float32)
        
        with torch.no_grad():
            tensor_x = torch.from_numpy(features).unsqueeze(0)
            prob = self.model(tensor_x).item()
            
        return prob
```

---

### 4.2 Dynamic Regime Classifier
The bot must classify market conditions dynamically to adjust sizing and score thresholds. This daemon computes the volatility z-score, the ATR ratio, and trend strength to transition between states.

#### State Machine Logic:
- If `Volatility Z-Score > 2.0` and `ATR Ratio > 1.8` → `VOLATILE_CHAOS`
- If `ATR Ratio < 0.6` and `Bollinger Width < 15-day average` → `COMPRESSION`
- If `Trend Strength (ADX equivalent) > 25` and `RSI has stayed > 60 or < 40 for 5+ candles` → `TRENDING`
- Else → `MEAN_REVERTING`

#### Python Implementation Blueprint for Claude Code:
```python
# core/analytics/regime_classifier.py
import numpy as np
import logging

log = logging.getLogger("zisi.regime")

class RegimeClassifier:
    def __init__(self, lookback=14):
        self.lookback = lookback
        
    def classify(self, klines) -> str:
        """
        klines: list of [time, open, high, low, close, volume]
        """
        if len(klines) < self.lookback + 10:
            return "MEAN_REVERTING"
            
        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        
        # Calculate ATR
        trs = []
        for i in range(1, len(klines)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        
        current_atr = np.mean(trs[-self.lookback:])
        baseline_atr = np.mean(trs[:-self.lookback]) if len(trs) > self.lookback else current_atr
        atr_ratio = current_atr / baseline_atr if baseline_atr > 0 else 1.0
        
        # Calculate Volatility Z-score (standard deviation of returns)
        returns = np.diff(closes) / closes[:-1]
        current_vol = np.std(returns[-self.lookback:])
        baseline_vol = np.std(returns) if len(returns) > 0 else current_vol
        vol_mean = np.mean([np.std(returns[i:i+self.lookback]) for i in range(len(returns)-self.lookback)]) if len(returns) > self.lookback * 2 else current_vol
        vol_std = np.std([np.std(returns[i:i+self.lookback]) for i in range(len(returns)-self.lookback)]) if len(returns) > self.lookback * 2 else 0.01
        
        vol_zscore = (current_vol - vol_mean) / vol_std if vol_std > 0 else 0.0
        
        # Calculate Simple Trend Strength (ADX-equivalent proxy)
        # Ratio of net move to path length
        net_move = abs(closes[-1] - closes[-self.lookback])
        path_length = np.sum(np.abs(np.diff(closes[-self.lookback:])))
        efficiency_ratio = net_move / path_length if path_length > 0 else 0.0
        
        # Classification Rules
        if vol_zscore > 2.0 or atr_ratio > 1.8:
            regime = "VOLATILE_CHAOS"
        elif atr_ratio < 0.65 or (efficiency_ratio < 0.15 and vol_zscore < -1.0):
            regime = "COMPRESSION"
        elif efficiency_ratio > 0.45 and (closes[-1] > np.mean(closes[-self.lookback:]) or closes[-1] < np.mean(closes[-self.lookback:])):
            regime = "TRENDING"
        else:
            regime = "MEAN_REVERTING"
            
        log.info("[REGIME] ATR Ratio: %.2f, Vol Z-Score: %.2f, Efficiency: %.2f → Mode: %s",
                 atr_ratio, vol_zscore, efficiency_ratio, regime)
        return regime
```

---

### 4.3 Fear & Greed Sentiment Daemon
At extremes of market sentiment, price distributions exhibit heavy tails (fat tails) and standard Kelly sizing is prone to underestimating risk. When retail greed is near 90 or panic is below 10, the likelihood of flash cascades increases.

This daemon queries the Fear & Greed Index API and returns a sizing multiplier.

#### Integration Point:
Modify `compute_size()` in `core/engine/updown_engine.py` or `app/main.py` to scale down the final position sizes when sentiment is over-extended.

```python
# core/analytics/sentiment_daemon.py
import aiohttp
import logging

log = logging.getLogger("zisi.sentiment")

class SentimentDaemon:
    def __init__(self):
        self.api_url = "https://api.alternative.me/fng/?limit=1"
        self.cached_fng = 50.0  # default neutral
        
    async def update_fng(self, session: aiohttp.ClientSession):
        """Run once every 4 hours (index updates daily, but slow-polling prevents missed ticks)"""
        try:
            async with session.get(self.api_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = float(data['data'][0]['value'])
                    self.cached_fng = val
                    log.info("[SENTIMENT] Fear & Greed Index updated: %.1f", val)
                else:
                    log.warning("[SENTIMENT] Failed to fetch F&G, code: %d", resp.status)
        except Exception as e:
            log.error("[SENTIMENT] Error fetching F&G: %s", e)
            
    def get_size_multiplier(self) -> float:
        """
        Scale down risk multiplier during extreme sentiment conditions.
        Extreme Greed (>85) or Extreme Fear (<15) signals reversal danger.
        """
        fng = self.cached_fng
        if fng >= 90 or fng <= 10:
            return 0.50  # Cut sizing in half (extreme danger)
        elif fng >= 80 or fng <= 20:
            return 0.75  # Cautious sizing reduction
        return 1.00  # Normal operation
```

---

### 4.4 Claude Code Integration Plan
To install these features:
1. **Regime Classifier**: Wire into the main loop in `app/main.py`. Call it every 5 minutes at candle open, saving the current state to `regime_status.json`.
2. **ANN Score Predictor**: Run a training script in the background on the `ml_training_data.jsonl` (contains 114k entries of historical feature snapshots). Save the weights, load them in `ANNEngine`, and use them to override the heuristic score when `is_loaded = True`.
3. **Sentiment Daemon**: Call `update_fng` in the background scheduler. Multiply `raw_bet_usd` in `app/main.py:484` by `sentiment_daemon.get_size_multiplier()`.
