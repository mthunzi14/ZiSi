import os
import numpy as np
import logging

log = logging.getLogger("zisi.ai_injector")

class LazyAIInjector:
    """
    LazyAIInjector - Defers the heavy loading of PyTorch and the LSTM model architecture
    until the first market prediction is requested. This eliminates startup latency
    and prevents CUDA dll-loading freezes on Windows/Linux host systems.
    """
    def __init__(self):
        self._actual_injector = None

    def _init_actual(self):
        if self._actual_injector is not None:
            return

        log.info("[AI Injector] Initializing PyTorch LSTM model core in-memory...")
        import torch
        import torch.nn as nn

        class PricePredictorLSTM(nn.Module):
            def __init__(self, input_size=5, hidden_size=64, num_layers=2):
                super(PricePredictorLSTM, self).__init__()
                self.hidden_size = hidden_size
                self.num_layers = num_layers
                self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.1)
                self.fc = nn.Linear(hidden_size, 1)
                self.sigmoid = nn.Sigmoid()

            def forward(self, x):
                h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size).to(x.device)
                c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size).to(x.device)
                
                out, _ = self.lstm(x, (h0, c0))
                out = self.fc(out[:, -1, :])
                return self.sigmoid(out)

        class AIInjectorCore:
            def __init__(self, model_path=None):
                self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                self.model = PricePredictorLSTM().to(self.device)
                self.seq_length = 10
                self.input_size = 5  # (price_delta, ofi, rsi, momentum, volume)
                self.is_trained = False
                
                if model_path is None:
                    _base = os.path.join(os.path.dirname(__file__), "model.pth")
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
                        "[AI Injector] No pre-trained weights — observe-only mode (no trade veto/boost)"
                    )

            def predict(self, feature_sequence: list[list[float]]) -> float:
                if len(feature_sequence) < self.seq_length:
                    # Pad with zeros if sequence is too short
                    pad = [[0.0] * self.input_size] * (self.seq_length - len(feature_sequence))
                    feature_sequence = pad + feature_sequence
                elif len(feature_sequence) > self.seq_length:
                    feature_sequence = feature_sequence[-self.seq_length:]
                    
                tensor_input = torch.tensor([feature_sequence], dtype=torch.float32).to(self.device)
                
                with torch.no_grad():
                    prediction = self.model(tensor_input)
                    
                return prediction.item()

        self._actual_injector = AIInjectorCore()

    def predict(self, feature_sequence: list[list[float]]) -> float:
        self._init_actual()
        return self._actual_injector.predict(feature_sequence)

    @property
    def is_trained(self):
        self._init_actual()
        return self._actual_injector.is_trained

# Singleton instance to be used across the bot
injector = LazyAIInjector()
