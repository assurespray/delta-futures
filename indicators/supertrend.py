"""SuperTrend indicator implementation."""
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from indicators.base import BaseIndicator
from config.constants import SIGNAL_UPTREND, SIGNAL_DOWNTREND

logger = logging.getLogger(__name__)


class SuperTrend(BaseIndicator):
    """SuperTrend indicator based on ATR."""
    
    def __init__(self, atr_length: int, factor: float, name: str = "SuperTrend"):
        """
        Initialize SuperTrend indicator.
        
        Args:
            atr_length: ATR period length
            factor: Multiplier factor for ATR
            name: Indicator name
        """
        super().__init__(name)
        self.atr_length = atr_length
        self.factor = factor
    
    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate Average True Range (ATR).
        
        Args:
            df: DataFrame with OHLC data
        
        Returns:
            Series with ATR values
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range calculation
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR is the moving average of True Range
        atr = tr.rolling(window=self.atr_length).mean()
        
        return atr
    
    def calculate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate SuperTrend indicator.
        
        Formula:
        Basic Upperband = (HIGH + LOW) / 2 + (ATR × Factor)
        Basic Lowerband = (HIGH + LOW) / 2 - (ATR × Factor)
        
        Final Upperband = Basic Upperband < Previous Final Upperband OR Previous Close > Previous Final Upperband
                         ? Basic Upperband : Previous Final Upperband
        
        Final Lowerband = Basic Lowerband > Previous Final Lowerband OR Previous Close < Previous Final Lowerband
                         ? Basic Lowerband : Previous Final Lowerband
        
        SuperTrend = Close <= Final Upperband ? Final Upperband : Final Lowerband
        Signal = Close <= Final Upperband ? -1 (Downtrend) : 1 (Uptrend)
        
        Args:
            candles: List of OHLC candle data
        
        Returns:
            Dictionary with SuperTrend values and signal
        """
        try:
            # Convert to DataFrame
            df = self.candles_to_dataframe(candles)
            
            if len(df) < self.atr_length + 1:
                logger.warning(f"⚠️ Not enough data for {self.name}: need {self.atr_length + 1}, got {len(df)}")
                return None
            
            # Calculate ATR
            atr = self.calculate_atr(df)
            
            # Calculate basic bands
            hl_avg = (df['high'] + df['low']) / 2
            basic_upperband = hl_avg + (self.factor * atr)
            basic_lowerband = hl_avg - (self.factor * atr)
            
            # Initialize final bands
            final_upperband = pd.Series(index=df.index, dtype=float)
            final_lowerband = pd.Series(index=df.index, dtype=float)
            supertrend = pd.Series(index=df.index, dtype=float)
            signal = pd.Series(index=df.index, dtype=int)
            
            # Calculate final bands and SuperTrend
            for i in range(len(df)):
                if i == 0:
                    final_upperband.iloc[i] = basic_upperband.iloc[i]
                    final_lowerband.iloc[i] = basic_lowerband.iloc[i]
                else:
                    # Final Upperband logic
                    if (basic_upperband.iloc[i] < final_upperband.iloc[i-1]) or (df['close'].iloc[i-1] > final_upperband.iloc[i-1]):
                        final_upperband.iloc[i] = basic_upperband.iloc[i]
                    else:
                        final_upperband.iloc[i] = final_upperband.iloc[i-1]
                    
                    # Final Lowerband logic
                    if (basic_lowerband.iloc[i] > final_lowerband.iloc[i-1]) or (df['close'].iloc[i-1] < final_lowerband.iloc[i-1]):
                        final_lowerband.iloc[i] = basic_lowerband.iloc[i]
                    else:
                        final_lowerband.iloc[i] = final_lowerband.iloc[i-1]
                
                # SuperTrend and Signal
                if i == 0:
                    supertrend.iloc[i] = final_upperband.iloc[i]
                    signal.iloc[i] = SIGNAL_DOWNTREND
                else:
                    if supertrend.iloc[i-1] == final_upperband.iloc[i-1] and df['close'].iloc[i] <= final_upperband.iloc[i]:
                        supertrend.iloc[i] = final_upperband.iloc[i]
                        signal.iloc[i] = SIGNAL_DOWNTREND
                    elif supertrend.iloc[i-1] == final_upperband.iloc[i-1] and df['close'].iloc[i] > final_upperband.iloc[i]:
                        supertrend.iloc[i] = final_lowerband.iloc[i]
                        signal.iloc[i] = SIGNAL_UPTREND
                    elif supertrend.iloc[i-1] == final_lowerband.iloc[i-1] and df['close'].iloc[i] >= final_lowerband.iloc[i]:
                        supertrend.iloc[i] = final_lowerband.iloc[i]
                        signal.iloc[i] = SIGNAL_UPTREND
                    elif supertrend.iloc[i-1] == final_lowerband.iloc[i-1] and df['close'].iloc[i] < final_lowerband.iloc[i]:
                        supertrend.iloc[i] = final_upperband.iloc[i]
                        signal.iloc[i] = SIGNAL_DOWNTREND
            
            # Get latest values
            latest_idx = len(df) - 1
            latest_supertrend = float(supertrend.iloc[latest_idx])
            latest_signal = int(signal.iloc[latest_idx])
            latest_close = float(df['close'].iloc[latest_idx])
            
            result = {
                "indicator_name": self.name,
                "atr_length": self.atr_length,
                "factor": self.factor,
                "latest_close": round(latest_close, 2),
                "supertrend_value": round(latest_supertrend, 2),
                "signal": latest_signal,
                "signal_text": "Uptrend" if latest_signal == SIGNAL_UPTREND else "Downtrend",
                "atr": round(float(atr.iloc[latest_idx]), 2)
            }
            
            logger.info(f"✅ {self.name} calculated: Signal={result['signal_text']}, Value=${result['supertrend_value']}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            return None
          
