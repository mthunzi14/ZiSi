"""
session_manager.py — Dynamic Session-Adaptive Trading parameter governor.

Defines the active trading session based on the current UTC time and maps
session-specific RSI band multipliers, Kelly sizing multipliers, and required
spread discount hurdles.
"""
import datetime
from typing import TypedDict

class SessionParams(TypedDict):
    session_name: str
    rsi_band_multiplier: float
    sizing_mult: float
    discount_hurdle: float

class TradingSessionManager:
    @staticmethod
    def get_active_session_params(now_utc: datetime.datetime = None) -> SessionParams:
        """
        Determines the current active session based on UTC day/hour.
        - Weekends (Friday 22:00 to Sunday 22:00 UTC)
        - Weekday Late Session (22:00 to 24:00 UTC)
        - Weekday Asia Session (00:00 to 08:00 UTC)
        - Weekday EU/US Peak Session (08:00 to 22:00 UTC)
        """
        if now_utc is None:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            
        weekday = now_utc.weekday()  # Monday is 0, Sunday is 6
        hour = now_utc.hour
        
        # 1. Weekend: Friday 22:00 UTC to Sunday 22:00 UTC
        is_weekend = False
        if weekday == 4 and hour >= 22:
            is_weekend = True
        elif weekday == 5:
            is_weekend = True
        elif weekday == 6 and hour < 22:
            is_weekend = True
            
        if is_weekend:
            return {
                "session_name": "WEEKEND",
                "rsi_band_multiplier": 1.25,
                "sizing_mult": 0.65,
                "discount_hurdle": 0.08
            }
            
        # 2. Weekday Late Session: 22:00 to 24:00 UTC
        if hour >= 22:
            return {
                "session_name": "WEEKDAY_LATE",
                "rsi_band_multiplier": 1.20,
                "sizing_mult": 0.75,
                "discount_hurdle": 0.07
            }
            
        # 3. Weekday Asia Session: 00:00 to 08:00 UTC
        if 0 <= hour < 8:
            return {
                "session_name": "WEEKDAY_ASIA",
                "rsi_band_multiplier": 1.15,
                "sizing_mult": 0.80,
                "discount_hurdle": 0.06
            }
            
        # 4. Weekday EU/US Peak Session: 08:00 to 22:00 UTC
        return {
            "session_name": "WEEKDAY_PEAK",
            "rsi_band_multiplier": 1.00,
            "sizing_mult": 1.00,
            "discount_hurdle": 0.05
        }
