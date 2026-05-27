import pytest
import os
import sys
from pathlib import Path

# Add project root to path for local execution
sys.path.insert(0, str(Path(__file__).parent.parent))

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Safely override all environment keys to run unit tests in sandbox paper mode."""
    monkeypatch.setenv("BOT_MODE", "paper_trading")
    monkeypatch.setenv("ACCOUNT_BALANCE", "100.0")
    monkeypatch.setenv("POLYMARKET_GAMMA_API_URL", "https://gamma-api.polymarket.com")
    monkeypatch.setenv("POLYMARKET_DATA_API_URL", "https://data-api.polymarket.com")
    monkeypatch.setenv("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com")
    monkeypatch.setenv("RISK_PER_TRADE_PERCENT", "2.0")
    monkeypatch.setenv("MAX_SIMULTANEOUS_TRADES", "6")

@pytest.fixture
def mock_state_manager():
    """Mock state manager with mock methods for positions and balance."""
    class MockStateManager:
        def __init__(self):
            self.balance = 100.0
            self.positions = []

        def get_current_balance(self):
            return self.balance

        def get_closed_positions(self, limit=3):
            return self.positions

    return MockStateManager()
