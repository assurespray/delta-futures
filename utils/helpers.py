"""Helper utility functions."""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def format_number(value: float, decimals: int = 2) -> str:
    """
    Format number with thousand separators.
    
    Args:
        value: Number to format
        decimals: Decimal places
    
    Returns:
        Formatted string
    """
    return f"{value:,.{decimals}f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """
    Format percentage value.
    
    Args:
        value: Percentage value
        decimals: Decimal places
    
    Returns:
        Formatted string with % sign
    """
    return f"{value:.{decimals}f}%"


def truncate_string(text: str, max_length: int = 100) -> str:
    """
    Truncate string to max length.
    
    Args:
        text: String to truncate
        max_length: Maximum length
    
    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float.
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
    
    Returns:
        Float value
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert value to int.
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
    
    Returns:
        Integer value
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
      
