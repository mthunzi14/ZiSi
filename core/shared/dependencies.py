"""
dependencies.py - Shared architectural boundary to prevent circular dependencies.
Decouples core prediction and risk calculation logic from infrastructure state managers.
"""
from infrastructure.state.state_manager import get_progress_toward_phase2
