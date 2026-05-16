"""
ML training data pipeline.
Collects one record per trading cycle, targeting 50 labelled trades for Phase 2.

Phase 1 (< 50 labelled trades): Gemini confidence × 0.65 deflation
Phase 2 (≥ 50 labelled trades): Logistic regression calibration curve replaces deflation
Phase 3 (≥ 200 labelled trades): Gradient boosted model comparison + promotion

The model trains every 10 new labelled examples and persists to disk.
Startup loads any existing model automatically.
"""
import json
import logging
import pickle
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("zisi.ml")

_BASE_DIR = Path(__file__).parent
_TRAINING_FILE   = _BASE_DIR / "ml_training_data.jsonl"
_MODEL_FILE      = _BASE_DIR / "lr_model.pkl"
_SCALER_FILE     = _BASE_DIR / "lr_scaler.pkl"
_CALIB_FILE      = _BASE_DIR / "calibration_curve.json"
_MODEL_META_FILE = _BASE_DIR / "model_meta.json"

CYCLES_NEEDED          = 50   # cycles before Phase 2 data collection check
MIN_LABELLED_TO_TRAIN  = 50   # labelled trades needed before first model train
RETRAIN_EVERY_N_TRADES = 10   # retrain after every N new labelled examples

# Numeric features used for training (must all be present in labelled records)
_NUMERIC_FEATURES = [
    "gemini_confidence",
    "signal_confidence",
    "entry_price",
    "hold_hours",
    "position_size",
]

# ── Model cache (loaded once on startup) ──────────────────────────────────────
_model = None
_scaler = None
_model_meta: Dict = {}


class MLPipeline:
    def __init__(self):
        self._cycle_count: int = self._count_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect_cycle_data(self, cycle_metrics: Dict, signals: List[Dict]) -> None:
        """
        Append one training record for this cycle.
        Call at the end of each bot cycle.
        """
        try:
            utc_now = datetime.now(timezone.utc)
            record = {
                "timestamp": utc_now.isoformat(),
                "utc_hour": utc_now.hour,
                "signals_count": len(signals),
                "avg_confidence": self._avg_confidence(signals),
                "market_distribution": self._count_by_market(signals),
                "sentiment_distribution": self._count_by_sentiment(signals),
                "metrics": {
                    "hypothetical_trades": cycle_metrics.get("hypothetical_trades", 0),
                    "hypothetical_pnl": cycle_metrics.get("hypothetical_pnl", 0),
                    "matches": cycle_metrics.get("matched_events", 0),
                    "kalshi_matches": cycle_metrics.get("kalshi_matches", 0),
                    "liquidity_skips": cycle_metrics.get("liquidity_skips", 0),
                    "price_skips": cycle_metrics.get("price_skips", 0),
                },
                "top_signals": [
                    {
                        "confidence": s.get("confidence", s.get("sentiment_score", 0.5)),
                        "market": s.get("coin", "OTHER"),
                        "sentiment": s.get("sentiment", "NEUTRAL"),
                        "matched": bool(s.get("matched_event")),
                    }
                    for s in (signals or [])[:10]
                ],
            }

            with _TRAINING_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            self._cycle_count += 1
            pct = min(100, self._cycle_count / CYCLES_NEEDED * 100)
            log.info(
                "[ML] Data collected — cycle %d/%d (%.0f%%)",
                self._cycle_count, CYCLES_NEEDED, pct,
            )

        except Exception as e:
            log.warning("[ML] Collection error (non-fatal): %s", e)

    def get_progress(self) -> Dict:
        """Return progress across all three planned models."""
        cycles = self._count_existing()
        pct = min(100.0, cycles / CYCLES_NEEDED * 100)
        ready = cycles >= CYCLES_NEEDED

        models = {
            "confidence_to_accuracy": {
                "description": "Maps signal confidence → actual accuracy",
                "cycles_collected": cycles,
                "cycles_needed": CYCLES_NEEDED,
                "progress_percent": round(pct, 1),
                "ready": ready,
                "status": "ready" if ready else "collecting",
            },
            "utc_hour_to_quality": {
                "description": "Maps UTC hour → signal quality",
                "cycles_collected": cycles,
                "cycles_needed": CYCLES_NEEDED,
                "progress_percent": round(pct, 1),
                "ready": ready,
                "status": "ready" if ready else "collecting",
            },
            "market_to_returns": {
                "description": "Maps market type → expected P&L",
                "cycles_collected": cycles,
                "cycles_needed": CYCLES_NEEDED,
                "progress_percent": round(pct, 1),
                "ready": ready,
                "status": "ready" if ready else "collecting",
            },
        }
        return {
            "cycles_collected": cycles,
            "cycles_needed": CYCLES_NEEDED,
            "progress_percent": round(pct, 1),
            "models": models,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _count_existing(self) -> int:
        try:
            if _TRAINING_FILE.exists():
                return sum(1 for line in _TRAINING_FILE.open("r", encoding="utf-8") if line.strip())
        except Exception:
            pass
        return 0

    @staticmethod
    def _avg_confidence(signals: List[Dict]) -> float:
        if not signals:
            return 0.0
        vals = [
            float(s.get("confidence") or s.get("sentiment_score") or 0)
            for s in signals
        ]
        return round(sum(vals) / len(vals), 4)

    @staticmethod
    def _count_by_market(signals: List[Dict]) -> Dict:
        counts = {"BTC": 0, "ETH": 0, "OTHER": 0}
        for s in signals:
            coin = str(s.get("coin", "OTHER")).upper()
            if "BITCOIN" in coin or coin == "BTC":
                counts["BTC"] += 1
            elif "ETHEREUM" in coin or coin == "ETH":
                counts["ETH"] += 1
            else:
                counts["OTHER"] += 1
        return counts

    @staticmethod
    def _count_by_sentiment(signals: List[Dict]) -> Dict:
        counts = {"bullish": 0, "bearish": 0, "neutral": 0}
        for s in signals:
            sent = str(s.get("sentiment", "neutral")).lower()
            counts[sent] = counts.get(sent, 0) + 1
        return counts


# Module-level singleton
_ml_pipeline = MLPipeline()


def collect_cycle_data(cycle_metrics: Dict, signals: List[Dict]) -> None:
    """Convenience wrapper — call from main.py at end of each cycle."""
    _ml_pipeline.collect_cycle_data(cycle_metrics, signals)


def get_ml_progress() -> Dict:
    """Return ML pipeline progress — used by health.js via a file read."""
    progress = _ml_pipeline.get_progress()
    # Write to file so Node.js dashboard can read it without running Python
    try:
        progress_file = _BASE_DIR / "ml_progress.json"
        import json as _json
        with progress_file.open("w", encoding="utf-8") as fh:
            _json.dump(progress, fh)
    except Exception:
        pass
    return progress


# ---------------------------------------------------------------------------
# Signal → Outcome linker (closes the ML feedback loop)
# ---------------------------------------------------------------------------

_SIGNAL_EVAL_FILE  = _BASE_DIR / "signal_evaluations.jsonl"
_LOCAL_TRADES_FILE = _BASE_DIR / "zisi_local_trades.jsonl"
_LABELLED_FILE     = _BASE_DIR / "ml_labelled_outcomes.jsonl"


def link_trade_outcomes() -> int:
    """
    Join signal_evaluations.jsonl entries with closed trades in
    zisi_local_trades.jsonl by order_id, writing matched pairs to
    ml_labelled_outcomes.jsonl.

    This is the key step that turns raw cycle data into supervised training
    examples: (confidence, source_quality, regime, utc_hour, fng) → WIN/LOSS.

    Call once per cycle from main.py.  Idempotent — already-labelled order_ids
    are skipped.  Returns count of new labelled records created this call.
    """
    # 1. Load existing labelled order_ids to avoid duplicates
    existing_ids: set = set()
    if _LABELLED_FILE.exists():
        try:
            for line in _LABELLED_FILE.open("r", encoding="utf-8"):
                line = line.strip()
                if line:
                    try:
                        existing_ids.add(json.loads(line).get("order_id", ""))
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Load all closed trades from JSONL
    closed_trades: Dict[str, dict] = {}
    if _LOCAL_TRADES_FILE.exists():
        try:
            for line in _LOCAL_TRADES_FILE.open("r", encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    oid = r.get("order_id", "")
                    if oid and r.get("status", "").upper() == "CLOSED":
                        closed_trades[oid] = r
                except Exception:
                    pass
        except Exception:
            pass

    if not closed_trades:
        return 0

    # 3. Load signal evaluations keyed by order_id
    signal_evals: Dict[str, dict] = {}
    if _SIGNAL_EVAL_FILE.exists():
        try:
            for line in _SIGNAL_EVAL_FILE.open("r", encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    oid = r.get("order_id", "")
                    if oid and oid not in signal_evals:
                        signal_evals[oid] = r
                except Exception:
                    pass
        except Exception:
            pass

    # 4. Match and append labelled records
    new_labels = 0
    try:
        with _LABELLED_FILE.open("a", encoding="utf-8") as fh:
            for order_id, trade in closed_trades.items():
                if order_id in existing_ids:
                    continue

                sig   = signal_evals.get(order_id, {})
                profit = float(trade.get("profit", 0) or 0)
                entry_price = float(trade.get("entry_price", 0) or 0)
                exit_price_val = float(trade.get("exit_price", 0) or 0)
                direction = str(trade.get("direction", "YES")).upper()

                # Directional accuracy label:
                # For binary prediction markets, price movement in our direction = correct call.
                # YES trade: price went up = WIN. NO trade: price went down = WIN.
                # P&L sign is equivalent for simple binary markets but explicitly
                # using direction + price delta is more semantically correct.
                if entry_price > 0 and exit_price_val > 0:
                    if direction == "YES":
                        outcome = "WIN" if exit_price_val > entry_price else "LOSS"
                    elif direction == "NO":
                        # NO trade: profit when price drops (NO price rises as YES falls)
                        # exit_price stored is the NO side price, so same direction logic
                        outcome = "WIN" if profit > 0 else "LOSS"
                    else:
                        outcome = "WIN" if profit > 0 else "LOSS"
                else:
                    outcome = "WIN" if profit > 0 else "LOSS"

                # Surface the exit_reason so we can audit label quality
                exit_reason = trade.get("exit_reason", "UNKNOWN")

                record = {
                    "order_id":                order_id,
                    "market":                  trade.get("market", "POLYMARKET"),
                    "label":                   outcome,
                    "outcome":                 outcome,
                    "predicted_direction":     direction,
                    "exit_reason":             exit_reason,
                    "profit":                  round(profit, 4),
                    "profit_pct":              round(float(trade.get("profit_percent", 0) or 0), 2),
                    # ── Signal features for model training ───────────────────
                    "gemini_confidence":       float(sig.get("confidence") or trade.get("confidence") or 0.5),
                    "signal_confidence":       float(sig.get("confidence") or trade.get("confidence") or 0.5),
                    "gemini_deflated_confidence": round(
                        float(sig.get("confidence") or trade.get("confidence") or 0.5) * 0.65, 4
                    ),
                    "source_quality":          float(sig.get("source_quality", 0.7) or 0.7),
                    "sentiment":               sig.get("sentiment") or trade.get("sentiment", ""),
                    "platform":                trade.get("market", "POLYMARKET"),
                    "market_category":         trade.get("market_category") or sig.get("market_category", "CRYPTO"),
                    "kelly_fraction_used":     float(trade.get("kelly_fraction_used", 0) or 0),
                    # ── Market features ──────────────────────────────────────
                    "entry_price":             round(entry_price, 6),
                    "exit_price":              round(exit_price_val, 6),
                    "position_size":           float(trade.get("position_size") or trade.get("size") or 0),
                    "hold_hours":              float(trade.get("hold_hours", 0) or 0),
                    # ── Timestamps ───────────────────────────────────────────
                    "timestamp_entry":         sig.get("timestamp") or trade.get("open_time", ""),
                    "timestamp_exit":          trade.get("close_time") or trade.get("exit_time", ""),
                }
                fh.write(json.dumps(record) + "\n")
                existing_ids.add(order_id)
                new_labels += 1

    except Exception as exc:
        log.warning("[ML-LABEL] Failed to write labelled outcomes: %s", exc)
        return new_labels

    if new_labels:
        log.info("[ML-LABEL] ✅ Linked %d new trade outcome(s) to signals", new_labels)
        # Count total labelled records
        total = len(existing_ids)
        log.info("[ML-LABEL] Total labelled examples: %d", total)

        # Auto-trigger model training every RETRAIN_EVERY_N_TRADES new examples
        if new_labels > 0 and total >= MIN_LABELLED_TO_TRAIN:
            if total % RETRAIN_EVERY_N_TRADES < new_labels:
                log.info("[ML] %d labelled examples → triggering model training", total)
                train_model()

    return new_labels


# ---------------------------------------------------------------------------
# Model training — Phase 2 (logistic regression calibration)
# ---------------------------------------------------------------------------

def _load_labelled_dataset() -> Tuple[List, List]:
    """
    Load labelled examples from ml_labelled_outcomes.jsonl.
    Returns (X_rows, y_labels) where X_rows are feature dicts.
    Skips records with zero or missing entry_price (data quality issue).
    """
    X, y = [], []
    if not _LABELLED_FILE.exists():
        return X, y
    try:
        for line in _LABELLED_FILE.open("r", encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                # Skip corrupt records (entry_price = 0 means feature capture failed)
                if float(r.get("entry_price", 0) or 0) <= 0.0:
                    continue
                label = 1 if r.get("label", r.get("outcome", "LOSS")) == "WIN" else 0
                features = {f: float(r.get(f, 0) or 0) for f in _NUMERIC_FEATURES}
                X.append(features)
                y.append(label)
            except Exception:
                continue
    except Exception as exc:
        log.warning("[ML-TRAIN] Failed to read labelled dataset: %s", exc)
    return X, y


def train_model() -> bool:
    """
    Train a logistic regression model on labelled examples.
    Persists model + scaler to disk. Updates in-memory model cache.
    Returns True on success, False if not enough data or sklearn unavailable.
    """
    global _model, _scaler, _model_meta

    X_dicts, y = _load_labelled_dataset()
    if len(X_dicts) < MIN_LABELLED_TO_TRAIN:
        log.info(
            "[ML-TRAIN] Only %d valid labelled examples (need %d) — skipping",
            len(X_dicts), MIN_LABELLED_TO_TRAIN,
        )
        return False

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score, roc_auc_score
    except ImportError:
        log.warning("[ML-TRAIN] scikit-learn not installed (pip install scikit-learn)")
        return False

    try:
        # Build feature matrix
        feature_names = _NUMERIC_FEATURES
        X = np.array([[row.get(f, 0) for f in feature_names] for row in X_dicts])
        y_arr = np.array(y)

        # 80/20 train/val split (only if enough data)
        if len(X) >= 10:
            X_train, X_val, y_train, y_val = train_test_split(
                X, y_arr, test_size=0.20, random_state=42, stratify=y_arr if len(set(y_arr)) > 1 else None,
            )
        else:
            X_train, X_val, y_train, y_val = X, X, y_arr, y_arr

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_val_s   = scaler.transform(X_val)

        model = LogisticRegression(max_iter=1000, class_weight="balanced")
        model.fit(X_train_s, y_train)

        # Metrics
        y_pred = model.predict(X_val_s)
        acc = accuracy_score(y_val, y_pred)
        try:
            auc = roc_auc_score(y_val, model.predict_proba(X_val_s)[:, 1])
        except Exception:
            auc = 0.0

        # Persist
        with _MODEL_FILE.open("wb") as f:
            pickle.dump(model, f)
        with _SCALER_FILE.open("wb") as f:
            pickle.dump(scaler, f)

        meta = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_examples": len(X),
            "n_train": len(X_train),
            "n_val": len(X_val),
            "accuracy": round(acc, 4),
            "auc": round(auc, 4),
            "feature_names": feature_names,
            "phase": "PHASE_2_CALIBRATED" if len(X) >= MIN_LABELLED_TO_TRAIN else "PHASE_1",
            "model_type": "LogisticRegression",
        }
        _MODEL_META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        _model  = model
        _scaler = scaler
        _model_meta = meta

        log.info(
            "[ML-TRAIN] Model trained: %d examples | acc=%.1f%% | AUC=%.3f | phase=%s",
            len(X), acc * 100, auc, meta["phase"],
        )
        return True

    except Exception as exc:
        log.error("[ML-TRAIN] Training failed: %s", exc)
        return False


def load_model() -> bool:
    """
    Load persisted model + scaler from disk on startup.
    Returns True if a valid model was loaded.
    """
    global _model, _scaler, _model_meta

    if not _MODEL_FILE.exists() or not _SCALER_FILE.exists():
        log.info("[ML] No persisted model yet — Gemini deflation active (self-learning, upgrades at 50 examples)")
        return False

    try:
        with _MODEL_FILE.open("rb") as f:
            _model = pickle.load(f)
        with _SCALER_FILE.open("rb") as f:
            _scaler = pickle.load(f)
        if _MODEL_META_FILE.exists():
            _model_meta = json.loads(_MODEL_META_FILE.read_text(encoding="utf-8"))
        log.info(
            "[ML] Model loaded from disk | acc=%.1f%% | AUC=%.3f | trained %s",
            _model_meta.get("accuracy", 0) * 100,
            _model_meta.get("auc", 0),
            _model_meta.get("trained_at", "unknown")[:10],
        )
        return True
    except Exception as exc:
        log.warning("[ML] Model load failed: %s — reverting to Gemini deflation", exc)
        _model = None
        _scaler = None
        return False


def predict_win_probability(feature_snapshot: dict) -> Optional[float]:
    """
    Use the trained model to estimate win probability for a candidate trade.
    Returns None if model not loaded (Phase 1 — caller uses Gemini deflation).
    Returns float 0-1 in Phase 2.
    """
    if _model is None or _scaler is None:
        return None

    try:
        import numpy as np
        features = [feature_snapshot.get(f, 0) or 0 for f in _NUMERIC_FEATURES]
        X = np.array([features])
        X_scaled = _scaler.transform(X)
        prob = float(_model.predict_proba(X_scaled)[0][1])
        return round(prob, 4)
    except Exception as exc:
        log.debug("[ML-PREDICT] Prediction failed: %s", exc)
        return None


def get_blended_confidence(
    gemini_confidence: float,
    feature_snapshot: dict,
    gemini_deflation: float = 0.65,
) -> Tuple[float, str]:
    """
    Blend Gemini confidence with model probability (when model is loaded).

    Phase 1: return gemini × 0.65 (deflated), source="PHASE_1_DEFLATED"
    Phase 2: return 0.5 × gemini + 0.5 × model_prob, source="PHASE_2_BLENDED"

    Returns (confidence_0_to_1, source_tag) for logging.
    """
    model_prob = predict_win_probability(feature_snapshot)

    if model_prob is None:
        # Phase 1 — no model yet
        deflated = round(gemini_confidence * gemini_deflation, 4)
        return deflated, "PHASE_1_DEFLATED"

    # Phase 2 — blend
    # Gemini is 0-10 scale, normalize to 0-1 first
    gem_norm = min(1.0, gemini_confidence / 10.0) if gemini_confidence > 1 else gemini_confidence
    blended = round(0.5 * gem_norm + 0.5 * model_prob, 4)

    log.info(
        "[ML-BLEND] PHASE_2 | gemini=%.3f model=%.3f blended=%.3f",
        gem_norm, model_prob, blended,
    )
    return blended, "PHASE_2_BLENDED"


def get_model_status() -> dict:
    """Return current model state for dashboard display."""
    labelled_count = 0
    try:
        if _LABELLED_FILE.exists():
            labelled_count = sum(1 for l in _LABELLED_FILE.open() if l.strip())
    except Exception:
        pass

    return {
        "model_loaded": _model is not None,
        "model_type": _model_meta.get("model_type", "none"),
        "phase": _model_meta.get("phase", "PHASE_1_UNCALIBRATED"),
        "accuracy": _model_meta.get("accuracy", 0),
        "auc": _model_meta.get("auc", 0),
        "trained_at": _model_meta.get("trained_at", ""),
        "n_training_examples": _model_meta.get("n_examples", 0),
        "n_labelled_total": labelled_count,
        "examples_until_training": max(0, MIN_LABELLED_TO_TRAIN - labelled_count),
        "next_retrain_in": RETRAIN_EVERY_N_TRADES - (labelled_count % RETRAIN_EVERY_N_TRADES) if labelled_count >= MIN_LABELLED_TO_TRAIN else None,
    }
