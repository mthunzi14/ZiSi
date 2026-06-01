"""
training_pipeline.py — Enhanced LSTM Training Pipeline for ZiSi Bot.

Trains a PyTorch LSTM on historical signal evaluation data to predict
directional outcomes (UP/DOWN) for crypto binary-options trades.

Data sources:
    - signal_evaluations.jsonl  (per-signal snapshots with indicators)
    - ml_training_data.jsonl    (cycle-level aggregated records)

Public API:
    train_model()        → dict   Train LSTM and return metrics.
    load_training_data() → tuple  Return (X, y) numpy arrays.
    evaluate_model()     → dict   Run validation and return accuracy/loss.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("zisi.training_pipeline")

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_SIGNAL_EVAL_FILE = _BASE_DIR / "signal_evaluations.jsonl"
_ML_TRAINING_FILE = _BASE_DIR / "ml_training_data.jsonl"
_MODEL_WEIGHTS    = Path(__file__).resolve().parent / "trained_model.pt"
_METRICS_FILE     = Path(__file__).resolve().parent / "training_metrics.json"

# ── Feature & training constants ──────────────────────────────────────────────
FEATURE_NAMES: List[str] = ["rsi", "momentum", "ofi", "volume", "price_delta"]
REGIME_LABELS: List[str] = ["TRENDING", "MEAN_REVERTING", "VOLATILE_CHAOS", "COMPRESSION"]
SEQUENCE_LENGTH: int = 10
N_BASE_FEATURES: int = len(FEATURE_NAMES)
N_REGIME_FEATURES: int = len(REGIME_LABELS)
N_TOTAL_FEATURES: int = N_BASE_FEATURES + N_REGIME_FEATURES  # 5 + 4 = 9

# Training hyperparameters
HIDDEN_SIZE: int = 64
DROPOUT: float = 0.3
DENSE_HIDDEN: int = 32
LEARNING_RATE: float = 0.001
MAX_EPOCHS: int = 100
PATIENCE: int = 10
TRAIN_SPLIT: float = 0.80
BATCH_SIZE: int = 32

# ── Guarded PyTorch import ────────────────────────────────────────────────────
_TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    _TORCH_AVAILABLE = True
except ImportError:
    log.warning(
        "[LSTM-PIPELINE] PyTorch not installed — LSTM training disabled. "
        "Install with: pip install torch"
    )

# ── Guarded numpy import ─────────────────────────────────────────────────────
_NUMPY_AVAILABLE = False
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    log.warning(
        "[LSTM-PIPELINE] numpy not installed — LSTM training disabled. "
        "Install with: pip install numpy"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Model Definition
# ══════════════════════════════════════════════════════════════════════════════

if _TORCH_AVAILABLE:
    class DirectionPredictorLSTM(nn.Module):
        """
        LSTM binary classifier for UP/DOWN direction prediction.

        Architecture
        ------------
        Input  : (batch, seq_len=10, n_features=9)
        LSTM   : 64 hidden units, 1 layer
        Dropout: 0.3
        Dense  : 64 → 32 → 1 (sigmoid)
        Output : scalar probability ∈ [0, 1]  (UP probability)
        """

        def __init__(
            self,
            input_size: int = N_TOTAL_FEATURES,
            hidden_size: int = HIDDEN_SIZE,
            dropout: float = DROPOUT,
            dense_hidden: int = DENSE_HIDDEN,
        ):
            super().__init__()
            self.hidden_size = hidden_size

            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=1,
                batch_first=True,
                dropout=0.0,  # single layer → dropout param unused; we add it manually
            )
            self.dropout = nn.Dropout(p=dropout)
            self.fc1 = nn.Linear(hidden_size, dense_hidden)
            self.relu = nn.ReLU()
            self.fc2 = nn.Linear(dense_hidden, 1)
            self.sigmoid = nn.Sigmoid()

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            # x: (batch, seq_len, input_size)
            h0 = torch.zeros(1, x.size(0), self.hidden_size, device=x.device)
            c0 = torch.zeros(1, x.size(0), self.hidden_size, device=x.device)
            lstm_out, _ = self.lstm(x, (h0, c0))
            # Take last time-step output
            last_hidden = lstm_out[:, -1, :]  # (batch, hidden_size)
            out = self.dropout(last_hidden)
            out = self.relu(self.fc1(out))
            out = self.sigmoid(self.fc2(out))  # (batch, 1)
            return out.squeeze(-1)


# ══════════════════════════════════════════════════════════════════════════════
# Data Loading & Feature Engineering
# ══════════════════════════════════════════════════════════════════════════════

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file and return list of parsed dicts, skipping bad lines."""
    records: List[Dict[str, Any]] = []
    if not path.exists():
        log.debug("[LSTM-PIPELINE] File not found: %s", path)
        return records
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    log.debug("[LSTM-PIPELINE] Skipped malformed line %d in %s", lineno, path.name)
    except Exception as exc:
        log.warning("[LSTM-PIPELINE] Error reading %s: %s", path.name, exc)
    return records


def _extract_features(record: Dict[str, Any]) -> Optional[List[float]]:
    """
    Extract the 5 base numeric features from a record.

    Looks for keys in multiple naming conventions:
        - Direct: rsi, momentum, ofi, volume, price_delta
        - Nested under 'indicators' or 'candle'
        - Aliased: sentiment_score → momentum, etc.

    Returns None if not enough features could be extracted.
    """
    indicators = record.get("indicators", {})
    candle = record.get("candle", {})

    def _get(key: str) -> float:
        """Try multiple locations for a feature value."""
        # Direct top-level
        val = record.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        # Nested in indicators
        val = indicators.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        # Nested in candle
        val = candle.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        return 0.0

    features = [_get(name) for name in FEATURE_NAMES]
    return features


def _extract_regime(record: Dict[str, Any]) -> List[float]:
    """
    One-hot encode the regime label.

    Returns a list of length 4: [TRENDING, MEAN_REVERTING, VOLATILE_CHAOS, COMPRESSION].
    Falls back to all-zeros if regime is unknown or absent.
    """
    regime_raw = str(
        record.get("regime")
        or record.get("market_regime")
        or record.get("indicators", {}).get("regime")
        or ""
    ).upper().strip()

    # Map common regime name variations
    regime_map = {
        "TRENDING": "TRENDING",
        "TREND": "TRENDING",
        "NORMAL": "TRENDING",
        "MEAN_REVERTING": "MEAN_REVERTING",
        "MEAN_REVERSION": "MEAN_REVERTING",
        "RANGE": "MEAN_REVERTING",
        "VOLATILE_CHAOS": "VOLATILE_CHAOS",
        "VOLATILE": "VOLATILE_CHAOS",
        "SHOCK": "VOLATILE_CHAOS",
        "COMPRESSION": "COMPRESSION",
        "COMPRESSED": "COMPRESSION",
    }
    regime = regime_map.get(regime_raw, "")

    one_hot = [0.0] * N_REGIME_FEATURES
    if regime in REGIME_LABELS:
        one_hot[REGIME_LABELS.index(regime)] = 1.0
    return one_hot


def _extract_label(record: Dict[str, Any]) -> Optional[int]:
    """
    Extract binary direction label: UP=1, DOWN=0.

    Checks multiple field names: outcome, label, direction, sentiment.
    Returns None if label cannot be determined.
    """
    # Explicit outcome field
    for key in ("outcome", "label", "direction"):
        val = record.get(key)
        if val is not None:
            val_upper = str(val).upper().strip()
            if val_upper in ("UP", "WIN", "YES", "BULLISH", "1"):
                return 1
            if val_upper in ("DOWN", "LOSS", "NO", "BEARISH", "0"):
                return 0

    # Fall back to sentiment field
    sentiment = str(record.get("sentiment", "")).lower().strip()
    if sentiment == "bullish":
        return 1
    if sentiment == "bearish":
        return 0

    return None


def _normalize_features(raw_features: "np.ndarray") -> "np.ndarray":
    """
    Min-max normalize each feature column to [0, 1] across the dataset.
    Handles constant columns (max == min) gracefully.

    Args:
        raw_features: (N, n_features) numpy array.
    Returns:
        Normalized copy of the same shape.
    """
    mins = raw_features.min(axis=0)
    maxs = raw_features.max(axis=0)
    ranges = maxs - mins
    # Avoid divide-by-zero for constant features
    ranges[ranges == 0] = 1.0
    return (raw_features - mins) / ranges


def _create_sequences(
    features: "np.ndarray",
    labels: "np.ndarray",
    seq_len: int = SEQUENCE_LENGTH,
) -> Tuple["np.ndarray", "np.ndarray"]:
    """
    Create sliding-window sequences from flat feature/label arrays.

    Args:
        features: (N, n_features) array.
        labels:   (N,) array.
        seq_len:  Window length (default 10).
    Returns:
        X: (N - seq_len + 1, seq_len, n_features)
        y: (N - seq_len + 1,)
    """
    n_samples = len(features)
    if n_samples < seq_len:
        log.warning(
            "[LSTM-PIPELINE] Not enough samples for sequences: %d < %d",
            n_samples, seq_len,
        )
        return np.empty((0, seq_len, features.shape[1])), np.empty((0,))

    X_seqs = []
    y_seqs = []
    for i in range(n_samples - seq_len + 1):
        X_seqs.append(features[i : i + seq_len])
        y_seqs.append(labels[i + seq_len - 1])  # label of the last candle in the window

    return np.array(X_seqs, dtype=np.float32), np.array(y_seqs, dtype=np.float32)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def load_training_data() -> Optional[Tuple["np.ndarray", "np.ndarray"]]:
    """
    Load and preprocess training data from both JSONL sources.

    Returns:
        (X, y) where X has shape (N_sequences, SEQUENCE_LENGTH, N_TOTAL_FEATURES)
        and y has shape (N_sequences,) with values 0 or 1.
        Returns None if dependencies are missing or no usable data found.
    """
    if not _NUMPY_AVAILABLE:
        log.warning("[LSTM-PIPELINE] numpy not available — cannot load training data")
        return None

    # Collect records from both sources
    all_records: List[Dict[str, Any]] = []
    all_records.extend(_read_jsonl(_SIGNAL_EVAL_FILE))
    all_records.extend(_read_jsonl(_ML_TRAINING_FILE))

    if not all_records:
        log.warning("[LSTM-PIPELINE] No records found in training data files")
        return None

    log.info(
        "[LSTM-PIPELINE] Loaded %d raw records (%d from signal_evaluations, %d from ml_training_data)",
        len(all_records),
        sum(1 for _ in _read_jsonl(_SIGNAL_EVAL_FILE)),
        sum(1 for _ in _read_jsonl(_ML_TRAINING_FILE)),
    )

    # Extract features + labels
    feature_rows: List[List[float]] = []
    label_list: List[int] = []
    skipped = 0

    for record in all_records:
        base_feat = _extract_features(record)
        if base_feat is None:
            skipped += 1
            continue

        label = _extract_label(record)
        if label is None:
            skipped += 1
            continue

        regime_feat = _extract_regime(record)
        full_features = base_feat + regime_feat
        feature_rows.append(full_features)
        label_list.append(label)

    if skipped > 0:
        log.info("[LSTM-PIPELINE] Skipped %d records (missing features or labels)", skipped)

    if len(feature_rows) < SEQUENCE_LENGTH:
        log.warning(
            "[LSTM-PIPELINE] Only %d usable records — need at least %d for one sequence",
            len(feature_rows), SEQUENCE_LENGTH,
        )
        return None

    raw_features = np.array(feature_rows, dtype=np.float32)
    labels_arr = np.array(label_list, dtype=np.float32)

    # Normalize base features to [0, 1]; regime columns are already 0/1
    base = raw_features[:, :N_BASE_FEATURES]
    regime = raw_features[:, N_BASE_FEATURES:]
    base_norm = _normalize_features(base)
    normalized = np.hstack([base_norm, regime])

    # Create sliding-window sequences
    X, y = _create_sequences(normalized, labels_arr, SEQUENCE_LENGTH)

    log.info(
        "[LSTM-PIPELINE] Data ready: %d sequences × %d steps × %d features | "
        "class balance: UP=%.1f%% DOWN=%.1f%%",
        X.shape[0], X.shape[1], X.shape[2],
        (y.sum() / len(y)) * 100,
        ((1 - y).sum() / len(y)) * 100,
    )
    return X, y


def train_model() -> Optional[Dict[str, Any]]:
    """
    Train the LSTM model on historical signal evaluation data.

    Workflow:
        1. Load & preprocess data from JSONL files.
        2. Split 80/20 into train/validation.
        3. Train with BCE loss, Adam optimizer, early stopping.
        4. Save weights to trained_model.pt and metrics to training_metrics.json.

    Returns:
        Dict with training metrics (epochs, loss, accuracy, etc.)
        or None if torch/numpy unavailable or insufficient data.
    """
    if not _TORCH_AVAILABLE or not _NUMPY_AVAILABLE:
        log.warning("[LSTM-PIPELINE] Required libraries missing — cannot train")
        return None

    # 1. Load data
    result = load_training_data()
    if result is None:
        return None
    X, y = result

    n_total = len(X)
    if n_total < 2:
        log.warning("[LSTM-PIPELINE] Not enough sequences to train: %d", n_total)
        return None

    # 2. Train/validation split
    split_idx = max(1, int(n_total * TRAIN_SPLIT))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    log.info(
        "[LSTM-PIPELINE] Split: train=%d, val=%d",
        len(X_train), len(X_val),
    )

    # Convert to tensors
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    X_train_t = torch.tensor(X_train, dtype=torch.float32, device=device)
    y_train_t = torch.tensor(y_train, dtype=torch.float32, device=device)
    X_val_t = torch.tensor(X_val, dtype=torch.float32, device=device)
    y_val_t = torch.tensor(y_val, dtype=torch.float32, device=device)

    train_dataset = TensorDataset(X_train_t, y_train_t)
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)

    # 3. Build model
    model = DirectionPredictorLSTM(
        input_size=N_TOTAL_FEATURES,
        hidden_size=HIDDEN_SIZE,
        dropout=DROPOUT,
        dense_hidden=DENSE_HIDDEN,
    ).to(device)

    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # 4. Training loop with early stopping
    best_val_loss = float("inf")
    best_epoch = 0
    patience_counter = 0
    best_state_dict = None
    history: Dict[str, List[float]] = {
        "train_loss": [],
        "val_loss": [],
        "val_accuracy": [],
    }

    log.info("[LSTM-PIPELINE] Training started — max %d epochs, patience=%d", MAX_EPOCHS, PATIENCE)

    try:
        for epoch in range(1, MAX_EPOCHS + 1):
            # ── Train ─────────────────────────────────────────────────
            model.train()
            epoch_loss = 0.0
            n_batches = 0
            for X_batch, y_batch in train_loader:
                optimizer.zero_grad()
                preds = model(X_batch)
                loss = criterion(preds, y_batch)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
                n_batches += 1

            avg_train_loss = epoch_loss / max(n_batches, 1)

            # ── Validate ──────────────────────────────────────────────
            model.eval()
            with torch.no_grad():
                val_preds = model(X_val_t)
                val_loss = criterion(val_preds, y_val_t).item()
                val_predicted_labels = (val_preds >= 0.5).float()
                val_correct = (val_predicted_labels == y_val_t).sum().item()
                val_accuracy = val_correct / max(len(y_val_t), 1)

            history["train_loss"].append(round(avg_train_loss, 6))
            history["val_loss"].append(round(val_loss, 6))
            history["val_accuracy"].append(round(val_accuracy, 4))

            # ── Early stopping check ──────────────────────────────────
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_epoch = epoch
                patience_counter = 0
                best_state_dict = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                patience_counter += 1

            if epoch % 10 == 0 or epoch == 1:
                log.info(
                    "[LSTM-PIPELINE] Epoch %3d/%d — train_loss=%.4f val_loss=%.4f "
                    "val_acc=%.1f%% patience=%d/%d",
                    epoch, MAX_EPOCHS, avg_train_loss, val_loss,
                    val_accuracy * 100, patience_counter, PATIENCE,
                )

            if patience_counter >= PATIENCE:
                log.info(
                    "[LSTM-PIPELINE] Early stopping at epoch %d (best=%d, val_loss=%.4f)",
                    epoch, best_epoch, best_val_loss,
                )
                break

        # 5. Restore best weights
        if best_state_dict is not None:
            model.load_state_dict(best_state_dict)

        # 6. Final validation metrics
        model.eval()
        with torch.no_grad():
            final_preds = model(X_val_t)
            final_labels = (final_preds >= 0.5).float()
            final_acc = (final_labels == y_val_t).sum().item() / max(len(y_val_t), 1)
            final_loss = criterion(final_preds, y_val_t).item()

        # 7. Save model weights
        try:
            _MODEL_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), str(_MODEL_WEIGHTS))
            log.info("[LSTM-PIPELINE] Model weights saved to %s", _MODEL_WEIGHTS)
        except Exception as exc:
            log.error("[LSTM-PIPELINE] Failed to save model weights: %s", exc)

        # 8. Save training metrics
        metrics: Dict[str, Any] = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "model_type": "DirectionPredictorLSTM",
            "input_features": N_TOTAL_FEATURES,
            "sequence_length": SEQUENCE_LENGTH,
            "hidden_size": HIDDEN_SIZE,
            "n_total_samples": int(n_total),
            "n_train": int(len(X_train)),
            "n_val": int(len(X_val)),
            "best_epoch": best_epoch,
            "total_epochs": len(history["train_loss"]),
            "best_val_loss": round(best_val_loss, 6),
            "final_val_loss": round(final_loss, 6),
            "final_val_accuracy": round(final_acc, 4),
            "class_balance": {
                "up_pct": round(float(y.sum() / len(y)) * 100, 1),
                "down_pct": round(float((1 - y).sum() / len(y)) * 100, 1),
            },
            "device": str(device),
            "hyperparameters": {
                "learning_rate": LEARNING_RATE,
                "batch_size": BATCH_SIZE,
                "dropout": DROPOUT,
                "patience": PATIENCE,
                "max_epochs": MAX_EPOCHS,
            },
            "history": history,
        }

        try:
            with _METRICS_FILE.open("w", encoding="utf-8") as fh:
                json.dump(metrics, fh, indent=2)
            log.info("[LSTM-PIPELINE] Training metrics saved to %s", _METRICS_FILE)
        except Exception as exc:
            log.error("[LSTM-PIPELINE] Failed to save metrics: %s", exc)

        log.info(
            "[LSTM-PIPELINE] ✅ Training complete — %d epochs | "
            "val_acc=%.1f%% | val_loss=%.4f | device=%s",
            metrics["total_epochs"],
            final_acc * 100,
            final_loss,
            device,
        )
        return metrics

    except Exception as exc:
        log.error("[LSTM-PIPELINE] Training failed with exception: %s", exc, exc_info=True)
        return None


def evaluate_model() -> Optional[Dict[str, Any]]:
    """
    Load the trained LSTM model and run validation on the full dataset.

    Returns:
        Dict with accuracy, loss, and per-class metrics,
        or None if model/data unavailable.
    """
    if not _TORCH_AVAILABLE or not _NUMPY_AVAILABLE:
        log.warning("[LSTM-PIPELINE] Required libraries missing — cannot evaluate")
        return None

    if not _MODEL_WEIGHTS.exists():
        log.warning("[LSTM-PIPELINE] No trained model found at %s", _MODEL_WEIGHTS)
        return None

    # Load data
    result = load_training_data()
    if result is None:
        return None
    X, y = result

    # Load model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        model = DirectionPredictorLSTM(
            input_size=N_TOTAL_FEATURES,
            hidden_size=HIDDEN_SIZE,
            dropout=DROPOUT,
            dense_hidden=DENSE_HIDDEN,
        ).to(device)
        model.load_state_dict(
            torch.load(str(_MODEL_WEIGHTS), map_location=device, weights_only=True)
        )
        model.eval()
    except Exception as exc:
        log.error("[LSTM-PIPELINE] Failed to load model for evaluation: %s", exc)
        return None

    # Evaluate on full dataset
    try:
        X_t = torch.tensor(X, dtype=torch.float32, device=device)
        y_t = torch.tensor(y, dtype=torch.float32, device=device)

        with torch.no_grad():
            preds = model(X_t)
            loss = nn.BCELoss()(preds, y_t).item()
            pred_labels = (preds >= 0.5).float()
            accuracy = (pred_labels == y_t).sum().item() / max(len(y_t), 1)

            # Per-class metrics
            up_mask = y_t == 1.0
            down_mask = y_t == 0.0
            up_correct = ((pred_labels == 1.0) & up_mask).sum().item()
            down_correct = ((pred_labels == 0.0) & down_mask).sum().item()
            n_up = up_mask.sum().item()
            n_down = down_mask.sum().item()

        eval_result: Dict[str, Any] = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "n_samples": len(y),
            "loss": round(loss, 6),
            "accuracy": round(accuracy, 4),
            "accuracy_pct": round(accuracy * 100, 1),
            "up_recall": round(up_correct / max(n_up, 1), 4),
            "down_recall": round(down_correct / max(n_down, 1), 4),
            "n_up": int(n_up),
            "n_down": int(n_down),
            "device": str(device),
        }

        log.info(
            "[LSTM-PIPELINE] Evaluation: acc=%.1f%% loss=%.4f | "
            "UP recall=%.1f%% (%d) DOWN recall=%.1f%% (%d)",
            eval_result["accuracy_pct"], loss,
            eval_result["up_recall"] * 100, int(n_up),
            eval_result["down_recall"] * 100, int(n_down),
        )
        return eval_result

    except Exception as exc:
        log.error("[LSTM-PIPELINE] Evaluation failed: %s", exc, exc_info=True)
        return None


def get_pipeline_status() -> Dict[str, Any]:
    """Return a summary of the training pipeline state for dashboard display."""
    status: Dict[str, Any] = {
        "torch_available": _TORCH_AVAILABLE,
        "numpy_available": _NUMPY_AVAILABLE,
        "model_exists": _MODEL_WEIGHTS.exists(),
        "model_path": str(_MODEL_WEIGHTS),
        "metrics_path": str(_METRICS_FILE),
    }

    # Count available records
    n_signal_eval = 0
    n_ml_training = 0
    try:
        if _SIGNAL_EVAL_FILE.exists():
            n_signal_eval = sum(1 for line in _SIGNAL_EVAL_FILE.open("r", encoding="utf-8") if line.strip())
    except Exception:
        pass
    try:
        if _ML_TRAINING_FILE.exists():
            n_ml_training = sum(1 for line in _ML_TRAINING_FILE.open("r", encoding="utf-8") if line.strip())
    except Exception:
        pass

    status["n_signal_eval_records"] = n_signal_eval
    status["n_ml_training_records"] = n_ml_training
    status["n_total_records"] = n_signal_eval + n_ml_training

    # Load saved metrics if present
    if _METRICS_FILE.exists():
        try:
            with _METRICS_FILE.open("r", encoding="utf-8") as fh:
                saved_metrics = json.load(fh)
            status["last_trained"] = saved_metrics.get("trained_at", "")
            status["last_accuracy"] = saved_metrics.get("final_val_accuracy", 0)
            status["last_val_loss"] = saved_metrics.get("final_val_loss", 0)
            status["last_epochs"] = saved_metrics.get("total_epochs", 0)
        except Exception:
            pass

    return status


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("Starting LSTM training pipeline...")
    metrics = train_model()
    if metrics:
        print("Training succeeded!")
        print(f"Val Accuracy: {metrics['final_val_accuracy']*100:.2f}%")
        print(f"Best Val Loss: {metrics['best_val_loss']:.6f}")
    else:
        print("Training failed or insufficient data.")
