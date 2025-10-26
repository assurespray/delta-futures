"""Base indicator class."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import pandas as pd


class BaseIndicator(ABC):
    """Abstract base class for all indicators."""
    
    def __init__(self, name: str):
        """
        Initialize indicator.
        
        Args:
            name: Indicator name
        """
        self.name = name
    
    @abstractmethod
    def calculate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate indicator values.
        
        Args:
            candles: List of OHLC candle data
        
        Returns:
            Dictionary with indicator values and signal
        """
        pass
    
    def candles_to_dataframe(self, candles: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Convert candles list to pandas DataFrame.
        
        Args:
            candles: List of OHLC candle data
        
        Returns:
            DataFrame with OHLC data
        """
        df = pd.DataFrame(candles)
        
        # Ensure proper data types
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        
        return df
      
