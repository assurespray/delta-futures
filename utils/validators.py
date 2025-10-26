"""Input validation utilities."""
import re
from typing import Optional


def validate_symbol(symbol: str) -> bool:
    """
    Validate trading symbol format.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        True if valid, False otherwise
    """
    if not symbol:
        return False
    
    # Should be alphanumeric, 3-10 characters
    if not re.match(r'^[A-Z0-9]{3,10}$', symbol):
        return False
    
    return True


def validate_lot_size(lot_size: int, min_size: int = 1, max_size: int = 10000) -> bool:
    """
    Validate lot size.
    
    Args:
        lot_size: Lot size to validate
        min_size: Minimum allowed size
        max_size: Maximum allowed size
    
    Returns:
        True if valid, False otherwise
    """
    if not isinstance(lot_size, int):
        return False
    
    if lot_size < min_size or lot_size > max_size:
        return False
    
    return True


def validate_timeframe(timeframe: str) -> bool:
    """
    Validate timeframe string.
    
    Args:
        timeframe: Timeframe to validate
    
    Returns:
        True if valid, False otherwise
    """
    valid_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    return timeframe in valid_timeframes


def validate_api_key(api_key: str) -> bool:
    """
    Validate API key format.
    
    Args:
        api_key: API key to validate
    
    Returns:
        True if valid, False otherwise
    """
    if not api_key or len(api_key) < 10:
        return False
    
    return True


def validate_direction(direction: str) -> bool:
    """
    Validate trading direction.
    
    Args:
        direction: Direction to validate
    
    Returns:
        True if valid, False otherwise
    """
    valid_directions = ["both", "long_only", "short_only"]
    return direction in valid_directions
  
