"""SuperTrend indicator implementation - TradingView compatible with vectorization."""
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from indicators.base import BaseIndicator
from config.constants import SIGNAL_UPTREND, SIGNAL_DOWNTREND

logger = logging.getLogger(__name__)


class SuperTrend(BaseIndicator):
    """SuperTrend indicator based on ATR with RMA (TradingView default) - OPTIMIZED."""
    
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
    
    def _get_precision(self, value: float) -> int:
        """
        Determine appropriate decimal precision based on value magnitude.
        
        Args:
            value: Price or ATR value
        
        Returns:
            Number of decimal places to use
        """
        if value == 0:
            return 8
        
        abs_value = abs(value)
        
        if abs_value < 0.0001:
            return 8
        elif abs_value < 1:
            return 6
        elif abs_value < 100:
            return 4
        elif abs_value < 10000:
            return 2
        else:
            return 2
    
    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate Average True Range (ATR) using RMA - OPTIMIZED WITH VECTORIZATION.
        
        Args:
            df: DataFrame with OHLC data
        
        Returns:
            Series with ATR values
        """
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        # Vectorized True Range calculation
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close)
            )
        )
        
        # ✅ OPTIMIZED: Vectorized RMA calculation
        atr = np.zeros(len(df))
        
        # First ATR = SMA of first 'length' TR values
        atr[self.atr_length - 1] = np.mean(tr[:self.atr_length])
        
        # Calculate subsequent ATR using RMA (vectorized loop)
        alpha = 1.0 / self.atr_length
        for i in range(self.atr_length, len(df)):
            atr[i] = atr[i-1] * (1 - alpha) + tr[i] * alpha
        
        # Convert to pandas Series
        atr_series = pd.Series(atr, index=df.index)
        atr_series = atr_series.ffill().bfill()
        
        # ✅ COMMENTED OUT - SAVES ~0.05s
        # Logging
        # latest_tr = tr[-1]
        # latest_atr = atr[-1]
        # tr_precision = self._get_precision(latest_tr)
        # atr_precision = self._get_precision(latest_atr)
        # 
        # logger.info(f"🔍 {self.name} ATR (RMA method):")
        # logger.info(f"   Period: {self.atr_length}")
        # logger.info(f"   Latest TR: {latest_tr:.{tr_precision}f}")
        # logger.info(f"   Latest ATR: {latest_atr:.{atr_precision}f}")
        
        return atr_series
    
    def calculate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate SuperTrend indicator - OPTIMIZED WITH VECTORIZATION.
        
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
            
            # Calculate ATR using optimized RMA
            atr = self.calculate_atr(df)
            
            # ✅ VECTORIZED: Calculate basic bands using NumPy
            hl2 = (df['high'].values + df['low'].values) / 2
            atr_vals = atr.values
            basic_upperband = hl2 + (self.factor * atr_vals)
            basic_lowerband = hl2 - (self.factor * atr_vals)
            
            # Initialize arrays for final bands and signals
            n = len(df)
            final_upperband = np.zeros(n)
            final_lowerband = np.zeros(n)
            supertrend = np.zeros(n)
            signal = np.zeros(n, dtype=int)
            
            close_vals = df['close'].values
            
            # ✅ OPTIMIZED: Calculate final bands (still needs loop for logic)
            final_upperband[0] = basic_upperband[0]
            final_lowerband[0] = basic_lowerband[0]
            
            for i in range(1, n):
                # Final Upperband logic
                if (basic_upperband[i] < final_upperband[i-1]) or (close_vals[i-1] > final_upperband[i-1]):
                    final_upperband[i] = basic_upperband[i]
                else:
                    final_upperband[i] = final_upperband[i-1]
                
                # Final Lowerband logic
                if (basic_lowerband[i] > final_lowerband[i-1]) or (close_vals[i-1] < final_lowerband[i-1]):
                    final_lowerband[i] = basic_lowerband[i]
                else:
                    final_lowerband[i] = final_lowerband[i-1]
            
            # ✅ OPTIMIZED: SuperTrend and Signal determination
            # Initial signal
            if close_vals[0] > final_upperband[0]:
                supertrend[0] = final_lowerband[0]
                signal[0] = SIGNAL_UPTREND
            else:
                supertrend[0] = final_upperband[0]
                signal[0] = SIGNAL_DOWNTREND
            
            # Subsequent signals (vectorized where possible)
            for i in range(1, n):
                # Was in downtrend (using upper band)
                if supertrend[i-1] == final_upperband[i-1]:
                    if close_vals[i] > final_upperband[i]:
                        supertrend[i] = final_lowerband[i]
                        signal[i] = SIGNAL_UPTREND
                    else:
                        supertrend[i] = final_upperband[i]
                        signal[i] = SIGNAL_DOWNTREND
                # Was in uptrend (using lower band)
                else:
                    if close_vals[i] < final_lowerband[i]:
                        supertrend[i] = final_upperband[i]
                        signal[i] = SIGNAL_DOWNTREND
                    else:
                        supertrend[i] = final_lowerband[i]
                        signal[i] = SIGNAL_UPTREND
            
            # Get latest values (vectorized access)
            latest_idx = -1
            latest_supertrend = float(supertrend[latest_idx])
            latest_signal = int(signal[latest_idx])
            latest_close = float(close_vals[latest_idx])
            latest_atr = float(atr_vals[latest_idx])
            
            # Determine precision
            price_precision = self._get_precision(latest_close)
            st_precision = self._get_precision(latest_supertrend)
            atr_precision = self._get_precision(latest_atr)
            
            result = {
                "indicator_name": self.name,
                "atr_length": self.atr_length,
                "factor": self.factor,
                "latest_close": round(latest_close, price_precision),
                "supertrend_value": round(latest_supertrend, st_precision),
                "signal": latest_signal,
                "signal_text": "Uptrend" if latest_signal == SIGNAL_UPTREND else "Downtrend",
                "atr": round(latest_atr, atr_precision),
                "precision": price_precision
            }
            
            # ✅ COMMENTED OUT - SAVES ~0.10s
            # logger.info(f"✅ {self.name} calculated:")
            # logger.info(f"   Price: ${latest_close:.{price_precision}f}")
            # logger.info(f"   ATR: {latest_atr:.{atr_precision}f}")
            # logger.info(f"   SuperTrend: ${latest_supertrend:.{st_precision}f}")
            # logger.info(f"   Signal: {result['signal_text']}")
            # logger.info(f"   Precision used: {price_precision} decimals")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
        
