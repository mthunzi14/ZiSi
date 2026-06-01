import os
import numpy as np
import logging

log = logging.getLogger("zisi.ai_injector")

class LazyAIInjector:
    """
    LazyAIInjector - Defers the heavy loading of PyTorch and the LSTM model architecture
    until the first prediction is requested. Resolves neural network architecture mismatches.
    """
    def __init__(self):
        self._actual_injector = None

    def _init_actual(self):
        if self._actual_injector is not None:
            return

        log.info("[AI Injector] Initializing PyTorch LSTM model core in-memory...")
        import torch
        import torch.nn as nn

        # Try to import DirectionPredictorLSTM from our training pipeline
        try:
            from core.ml.training_pipeline import DirectionPredictorLSTM
        except ImportError as e:
            log.error("[AI Injector] Failed to import DirectionPredictorLSTM: %s. Using inline fallback.", e)
            class DirectionPredictorLSTM(nn.Module):
                def __init__(self, input_size=9, hidden_size=64, dropout=0.3, dense_hidden=32):
                    super().__init__()
                    self.hidden_size = hidden_size
                    self.lstm = nn.LSTM(input_size, hidden_size, num_layers=1, batch_first=True)
                    self.dropout = nn.Dropout(p=dropout)
                    self.fc1 = nn.Linear(hidden_size, dense_hidden)
                    self.relu = nn.ReLU()
                    self.fc2 = nn.Linear(dense_hidden, 1)
                    self.sigmoid = nn.Sigmoid()

                def forward(self, x):
                    h0 = torch.zeros(1, x.size(0), self.hidden_size, device=x.device)
                    c0 = torch.zeros(1, x.size(0), self.hidden_size, device=x.device)
                    lstm_out, _ = self.lstm(x, (h0, c0))
                    last_hidden = lstm_out[:, -1, :]
                    out = self.dropout(last_hidden)
                    out = self.relu(self.fc1(out))
                    out = self.sigmoid(self.fc2(out))
                    return out.squeeze(-1)

        class AIInjectorCore:
            def __init__(self, model_path=None):
                self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                # Instantiate model with the exact dimensions used in training
                self.model = DirectionPredictorLSTM(
                    input_size=9,
                    hidden_size=64,
                    dropout=0.3,
                    dense_hidden=32
                ).to(self.device)
                self.seq_length = 10
                self.input_size = 9  # (5 indicators + 4 regime one-hot)
                self.is_trained = False
                
                if model_path is None:
                    # Aligned to load trained_model.pt saved by training_pipeline.py
                    _base = os.path.join(os.path.dirname(__file__), "trained_model.pt")
                    model_path = _base if os.path.exists(_base) else None

                # Load weights if available; otherwise observe-only
                if model_path and os.path.exists(model_path):
                    try:
                        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
                        self.model.eval()
                        self.is_trained = True
                        log.info("[AI Injector] Loaded trained PyTorch LSTM from %s on %s", model_path, self.device)
                    except Exception as e:
                        log.error("[AI Injector] Failed to load model weights: %s", e)
                else:
                    self.model.eval()
                    log.warning(
                        "[AI Injector] No pre-trained weights found at %s — observe-only mode (no trade veto/boost)",
                        model_path
                    )

            def predict(self, feature_sequence: list[list[float]], regime: str = "TRENDING") -> float:
                # 1. One-hot encode active regime
                regime_upper = str(regime).upper().strip()
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
                regime_name = regime_map.get(regime_upper, "TRENDING")
                regime_labels = ["TRENDING", "MEAN_REVERTING", "VOLATILE_CHAOS", "COMPRESSION"]
                
                one_hot = [0.0] * 4
                if regime_name in regime_labels:
                    one_hot[regime_labels.index(regime_name)] = 1.0
                
                # 2. Append one-hot encoding to each step in the sequence
                expanded_seq = []
                for step in feature_sequence:
                    # Slice to ensure exactly 5 base features before appending one-hot
                    base_features = list(step[:5])
                    if len(base_features) < 5:
                        base_features = base_features + [0.0] * (5 - len(base_features))
                    expanded_seq.append(base_features + one_hot)
                
                feature_sequence = expanded_seq
                
                if len(feature_sequence) < self.seq_length:
                    pad = [[0.0] * self.input_size] * (self.seq_length - len(feature_sequence))
                    feature_sequence = pad + feature_sequence
                elif len(feature_sequence) > self.seq_length:
                    feature_sequence = feature_sequence[-self.seq_length:]
                    
                tensor_input = torch.tensor([feature_sequence], dtype=torch.float32).to(self.device)
                
                with torch.no_grad():
                    prediction = self.model(tensor_input)
                    
                return float(prediction.item())

        self._actual_injector = AIInjectorCore()

    def predict(self, feature_sequence: list[list[float]], regime: str = "TRENDING") -> float:
        self._init_actual()
        return self._actual_injector.predict(feature_sequence, regime)

    @property
    def is_trained(self):
        self._init_actual()
        return self._actual_injector.is_trained

# Singleton instance to be used across the bot
injector = LazyAIInjector()
