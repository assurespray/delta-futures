"""SuperTrend indicator implementation - TradingView compatible."""
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from indicators.base import BaseIndicator
from config.constants import SIGNAL_UPTREND, SIGNAL_DOWNTREND

logger = logging.getLogger(__name__)


class SuperTrend(BaseIndicator):
    """SuperTrend indicator based on ATR with RMA (TradingView default)."""
    
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
        Calculate Average True Range (ATR) using RMA (Relative Moving Average).
        This EXACTLY matches TradingView's default SuperTrend calculation.
        
        RMA Formula (Wilder's Smoothing):
        - First value: SMA of first 'length' TR values
        - Subsequent: (Previous RMA * (length-1) + Current TR) / length
        
        This is different from EWM!
        
        Args:
            df: DataFrame with OHLC data
        
        Returns:
            Series with ATR values
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range calculation
        # TR = max(H-L, |H-C_prev|, |L-C_prev|)
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Initialize ATR series
        atr = pd.Series(index=df.index, dtype=float)
        
        # Calculate first ATR as SMA of first 'length' TR values
        # This is the initialization that TradingView uses
        atr.iloc[self.atr_length - 1] = tr.iloc[:self.atr_length].mean()
        
        # Calculate subsequent ATR values using RMA (Wilder's smoothing)
        # RMA[i] = (RMA[i-1] * (length-1) + TR[i]) / length
        for i in range(self.atr_length, len(df)):
            atr.iloc[i] = (atr.iloc[i-1] * (self.atr_length - 1) + tr.iloc[i]) / self.atr_length
        
        # Fill initial NaN values
        atr = atr.ffill().bfill()
        
        logger.info(f"🔍 {self.name} ATR (RMA method):")
        logger.info(f"   Period: {self.atr_length}")
        logger.info(f"   Latest TR: {tr.iloc[-1]:.2f}")
        logger.info(f"   Latest ATR: {atr.iloc[-1]:.2f}")
        
        return atr
    
    def calculate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate SuperTrend indicator - TradingView compatible.
        
        Formula:
        - HL2 = (High + Low) / 2
        - Upper Band = HL2 + (Factor × ATR)
        - Lower Band = HL2 - (Factor × ATR)
        
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
            
            # Calculate ATR using RMA (TradingView's default method)
            atr = self.calculate_atr(df)
            
            # Calculate basic bands using median price (HL2)
            hl2 = (df['high'] + df['low']) / 2
            basic_upperband = hl2 + (self.factor * atr)
            basic_lowerband = hl2 - (self.factor * atr)
            
            # Initialize final bands
            final_upperband = pd.Series(index=df.index, dtype=float)
            final_lowerband = pd.Series(index=df.index, dtype=float)
            supertrend = pd.Series(index=df.index, dtype=float)
            signal = pd.Series(index=df.index, dtype=int)
            
            # Calculate final bands and SuperTrend
            for i in range(len(df)):
                # Skip if we don't have ATR yet
                if pd.isna(atr.iloc[i]) or pd.isna(basic_upperband.iloc[i]):
                    continue
                
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
                
                # SuperTrend and Signal determination
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
            
            # Clean data
            valid_idx = ~supertrend.isna()
            df_clean = df[valid_idx].copy()
            supertrend_clean = supertrend[valid_idx]
            signal_clean = signal[valid_idx]
            atr_clean = atr[valid_idx]
            
            if len(df_clean) == 0:
                logger.error(f"❌ No valid data after cleaning for {self.name}")
                return None
            
            # Get latest values
            latest_idx = len(df_clean) - 1
            latest_supertrend = float(supertrend_clean.iloc[latest_idx])
            latest_signal = int(signal_clean.iloc[latest_idx])
            latest_close = float(df_clean['close'].iloc[latest_idx])
            latest_atr = float(atr_clean.iloc[latest_idx])
            
            result = {
                "indicator_name": self.name,
                "atr_length": self.atr_length,
                "factor": self.factor,
                "latest_close": round(latest_close, 2),
                "supertrend_value": round(latest_supertrend, 2),
                "signal": latest_signal,
                "signal_text": "Uptrend" if latest_signal == SIGNAL_UPTREND else "Downtrend",
                "atr": round(latest_atr, 2)
            }
            
            logger.info(f"✅ {self.name} calculated:")
            logger.info(f"   Price: ${latest_close:.2f}")
            logger.info(f"   ATR: {latest_atr:.2f}")
            logger.info(f"   SuperTrend: ${latest_supertrend:.2f}")
            logger.info(f"   Signal: {result['signal_text']}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
                    
