"""Timeframe boundary validation utilities for Delta Exchange India."""
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def is_at_candle_boundary(timeframe: str, check_time: Optional[datetime] = None) -> bool:
    """
    Check if current time is at a candle boundary for the given timeframe.
    
    Supports ALL Delta Exchange India API timeframes:
    1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 3h, 4h, 6h, 12h, 1d, 1w, 7d
    
    Args:
        timeframe: Timeframe string (e.g., "15m", "4h", "1d")
        check_time: Time to check (defaults to UTC now)
    
    Returns:
        True if at candle boundary, False otherwise
    """
    if check_time is None:
        check_time = datetime.utcnow()
    
    minute = check_time.minute
    hour = check_time.hour
    day_of_week = check_time.weekday()  # 0 = Monday, 6 = Sunday
    
    # Minute-based timeframes (< 1 hour)
    if timeframe == "1m":
        return True  # Every minute is a boundary
    
    elif timeframe == "2m":
        return minute % 2 == 0
    
    elif timeframe == "3m":
        return minute % 3 == 0
    
    elif timeframe == "5m":
        return minute % 5 == 0
    
    elif timeframe == "10m":
        return minute % 10 == 0
    
    elif timeframe == "15m":
        return minute % 15 == 0
    
    elif timeframe == "30m":
        return minute % 30 == 0
    
    # Hour-based timeframes
    elif timeframe == "1h":
        return minute == 0
    
    elif timeframe == "2h":
        return minute == 0 and hour % 2 == 0
    
    elif timeframe == "3h":
        return minute == 0 and hour % 3 == 0
    
    elif timeframe == "4h":
        return minute == 0 and hour % 4 == 0
    
    elif timeframe == "6h":
        return minute == 0 and hour % 6 == 0
    
    elif timeframe == "12h":
        return minute == 0 and hour % 12 == 0
    
    # Day-based timeframes
    elif timeframe == "1d":
        return minute == 0 and hour == 0
    
    # Week-based timeframes (Monday 00:00 UTC)
    elif timeframe in ["1w", "7d"]:
        return minute == 0 and hour == 0 and day_of_week == 0
    
    else:
        logger.warning(f"⚠️ Unknown timeframe: {timeframe}, defaulting to allow check")
        return True


def get_next_boundary_time(timeframe: str, current_time: Optional[datetime] = None) -> datetime:
    """
    Get the next candle boundary time for the given timeframe.
    
    Args:
        timeframe: Timeframe string
        current_time: Current time (defaults to UTC now)
    
    Returns:
        Next boundary datetime
    """
    if current_time is None:
        current_time = datetime.utcnow()
    
    minute = current_time.minute
    hour = current_time.hour
    
    # Helper function to calculate next minute boundary
    def next_minute_boundary(interval: int) -> datetime:
        next_minute = ((minute // interval) + 1) * interval
        if next_minute >= 60:
            return current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return current_time.replace(minute=next_minute, second=0, microsecond=0)
    
    # Helper function to calculate next hour boundary
    def next_hour_boundary(interval: int) -> datetime:
        next_hour = ((hour // interval) + 1) * interval
        if next_hour >= 24:
            return current_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return current_time.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    
    # Minute-based timeframes
    if timeframe == "1m":
        return current_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
    elif timeframe == "2m":
        return next_minute_boundary(2)
    elif timeframe == "3m":
        return next_minute_boundary(3)
    elif timeframe == "5m":
        return next_minute_boundary(5)
    elif timeframe == "10m":
        return next_minute_boundary(10)
    elif timeframe == "15m":
        return next_minute_boundary(15)
    elif timeframe == "30m":
        return next_minute_boundary(30)
    
    # Hour-based timeframes
    elif timeframe == "1h":
        return current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif timeframe == "2h":
        return next_hour_boundary(2)
    elif timeframe == "3h":
        return next_hour_boundary(3)
    elif timeframe == "4h":
        return next_hour_boundary(4)
    elif timeframe == "6h":
        return next_hour_boundary(6)
    elif timeframe == "12h":
        return next_hour_boundary(12)
    
    # Day-based timeframes
    elif timeframe == "1d":
        return current_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    # Week-based timeframes
    elif timeframe in ["1w", "7d"]:
        days_until_monday = (7 - current_time.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        return current_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
    
    # Default: next minute
    return current_time + timedelta(minutes=1)


def get_timeframe_seconds(timeframe: str) -> int:
    """
    Get the number of seconds in a timeframe period.
    
    Args:
        timeframe: Timeframe string
    
    Returns:
        Number of seconds in one candle period
    """
    timeframe_map = {
        "1m": 60,
        "2m": 120,
        "3m": 180,
        "5m": 300,
        "10m": 600,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "3h": 10800,
        "4h": 14400,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400,
        "1w": 604800,
        "7d": 604800
    }
    
    return timeframe_map.get(timeframe, 60)


def get_timeframe_display_name(timeframe: str) -> str:
    """
    Get human-readable display name for timeframe.
    
    Args:
        timeframe: Timeframe string
    
    Returns:
        Display name
    """
    display_names = {
        "1m": "1 Minute",
        "2m": "2 Minutes",
        "3m": "3 Minutes",
        "5m": "5 Minutes",
        "10m": "10 Minutes",
        "15m": "15 Minutes",
        "30m": "30 Minutes",
        "1h": "1 Hour",
        "2h": "2 Hours",
        "3h": "3 Hours",
        "4h": "4 Hours",
        "6h": "6 Hours",
        "12h": "12 Hours",
        "1d": "1 Day",
        "1w": "1 Week",
        "7d": "7 Days"
    }
    
    return display_names.get(timeframe, timeframe)
  
