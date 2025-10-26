"""Timeframe-based scheduling handler."""
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
from config.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)


class TimeframeHandler:
    """Handle timeframe-specific operations."""
    
    @staticmethod
    def get_seconds(timeframe: str) -> int:
        """
        Get seconds for a timeframe.
        
        Args:
            timeframe: Timeframe string (1m, 5m, 15m, etc.)
        
        Returns:
            Seconds as integer
        """
        return TIMEFRAME_SECONDS.get(timeframe, 900)  # Default 15m
    
    @staticmethod
    def get_next_execution_time(timeframe: str) -> datetime:
        """
        Calculate next execution time aligned to timeframe.
        
        Args:
            timeframe: Timeframe string
        
        Returns:
            Next execution datetime
        """
        now = datetime.utcnow()
        seconds = TimeframeHandler.get_seconds(timeframe)
        
        # Align to timeframe boundary
        next_exec = now + timedelta(seconds=seconds)
        
        return next_exec
    
    @staticmethod
    def should_execute_now(timeframe: str, last_execution: datetime) -> bool:
        """
        Check if enough time has passed for next execution.
        
        Args:
            timeframe: Timeframe string
            last_execution: Last execution datetime
        
        Returns:
            True if should execute, False otherwise
        """
        now = datetime.utcnow()
        seconds = TimeframeHandler.get_seconds(timeframe)
        
        elapsed = (now - last_execution).total_seconds()
        
        return elapsed >= seconds
    
    @staticmethod
    def get_candle_start_time(timeframe: str) -> int:
        """
        Get start timestamp for fetching historical candles.
        
        Args:
            timeframe: Timeframe string
        
        Returns:
            Unix timestamp
        """
        seconds = TimeframeHandler.get_seconds(timeframe)
        now = datetime.utcnow()
        
        # Fetch last 100 candles worth of data
        start_time = now - timedelta(seconds=seconds * 100)
        
        return int(start_time.timestamp())
      
