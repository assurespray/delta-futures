"""SuperTrend indicator - TradingView compatible with CORRECT band persistence."""
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from config.constants import SIGNAL_UPTREND, SIGNAL_DOWNTREND

logger = logging.getLogger(__name__)


class SuperTrend:
    """SuperTrend indicator based on ATR with RMA (TradingView compatible)."""
    
    def __init__(self, atr_length: int, factor: float, name: str = "SuperTrend"):
        super().__init__()
        self.atr_length = atr_length
        self.factor = factor
        self.name = name
    
    def _get_precision(self, value: float) -> int:
        """Determine decimal precision based on value magnitude."""
        if value == 0:
            return 8
        abs_value = abs(value)
        if abs_value < 0.0001:
            return 8
        elif abs_value < 1:
            return 6
        elif abs_value < 100:
            return 4
        else:
            return 2
    
    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate ATR using RMA method (TradingView compatible)."""
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        # True Range calculation
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close)
            )
        )
        
        # RMA calculation (exponential average)
        atr = np.zeros(len(df))
        atr[self.atr_length - 1] = np.mean(tr[:self.atr_length])
        
        alpha = 1.0 / self.atr_length
        for i in range(self.atr_length, len(df)):
            atr[i] = atr[i-1] * (1 - alpha) + tr[i] * alpha
        
        atr_series = pd.Series(atr, index=df.index)
        atr_series = atr_series.ffill().bfill()
        
        latest_tr = tr[-1]
        latest_atr = atr[-1]
        tr_precision = self._get_precision(latest_tr)
        atr_precision = self._get_precision(latest_atr)
        
        logger.info(f"🔍 {self.name} ATR (RMA method):")
        logger.info(f"   Period: {self.atr_length}")
        logger.info(f"   Latest TR: {latest_tr:.{tr_precision}f}")
        logger.info(f"   Latest ATR: {latest_atr:.{atr_precision}f}")
        
        return atr_series
    
    def calculate(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        ✅ CORRECTED: Calculate SuperTrend with proper band persistence.
        
        KEY FIXES:
        1. Upper band can ONLY go DOWN or STAY SAME (never up immediately)
        2. Lower band can ONLY go UP or STAY SAME (never down immediately)
        3. This creates proper trend state persistence
        4. Eliminates false entry/exit signals
        """
        try:
            # Convert to DataFrame
            df = pd.DataFrame(candles)
            df['high'] = pd.to_numeric(df['high'])
            df['low'] = pd.to_numeric(df['low'])
            df['close'] = pd.to_numeric(df['close'])
            
            if len(df) < self.atr_length + 1:
                logger.warning(f"⚠️ Not enough data: need {self.atr_length + 1}, got {len(df)}")
                return None
            
            # Calculate ATR
            atr = self.calculate_atr(df)
            
            # Calculate basic bands
            hl2 = (df['high'].values + df['low'].values) / 2
            atr_vals = atr.values
            basic_upperband = hl2 + (self.factor * atr_vals)
            basic_lowerband = hl2 - (self.factor * atr_vals)
            
            n = len(df)
            close_vals = df['close'].values
            
            # Initialize arrays
            final_upperband = np.zeros(n)
            final_lowerband = np.zeros(n)
            supertrend = np.zeros(n)
            signal = np.zeros(n, dtype=int)
            
            # First candle initialization
            final_upperband[0] = basic_upperband[0]
            final_lowerband[0] = basic_lowerband[0]
            supertrend[0] = final_lowerband[0]
            signal[0] = SIGNAL_UPTREND
            
            # ✅ CORRECTED: Band persistence based on TREND STATE
            for i in range(1, n):
                # Determine current trend based on previous supertrend position
                was_uptrend = (supertrend[i-1] == final_lowerband[i-1])
                
                # ✅ CRITICAL FIX: Upper band persistence (CAN ONLY GO DOWN or STAY SAME)
                if basic_upperband[i] < final_upperband[i-1]:
                    # Basic band went down, reset to new lower value
                    final_upperband[i] = basic_upperband[i]
                else:
                    # Basic band went up, but upper band persists (doesn't go up immediately)
                    final_upperband[i] = final_upperband[i-1]
                
                # ✅ CRITICAL FIX: Lower band persistence (CAN ONLY GO UP or STAY SAME)
                if basic_lowerband[i] > final_lowerband[i-1]:
                    # Basic band went up, reset to new higher value
                    final_lowerband[i] = basic_lowerband[i]
                else:
                    # Basic band went down, but lower band persists (doesn't go down immediately)
                    final_lowerband[i] = final_lowerband[i-1]
                
                # ✅ CRITICAL FIX: Determine signal based on trend and break conditions
                if was_uptrend:
                    # Was in uptrend (using lower band)
                    if close_vals[i] < final_lowerband[i]:
                        # Broke below lower band → Switch to downtrend
                        supertrend[i] = final_upperband[i]
                        signal[i] = SIGNAL_DOWNTREND
                    else:
                        # Stayed above lower band → Stay in uptrend
                        supertrend[i] = final_lowerband[i]
                        signal[i] = SIGNAL_UPTREND
                else:
                    # Was in downtrend (using upper band)
                    if close_vals[i] > final_upperband[i]:
                        # Broke above upper band → Switch to uptrend
                        supertrend[i] = final_lowerband[i]
                        signal[i] = SIGNAL_UPTREND
                    else:
                        # Stayed below upper band → Stay in downtrend
                        supertrend[i] = final_upperband[i]
                        signal[i] = SIGNAL_DOWNTREND
            
            # Get latest values
            latest_idx = -1
            latest_supertrend = float(supertrend[latest_idx])
            latest_signal = int(signal[latest_idx])
            latest_close = float(close_vals[latest_idx])
            latest_atr = float(atr_vals[latest_idx])
            
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
            
            logger.info(f"✅ {self.name} calculated:")
            logger.info(f"   Price: ${latest_close:.{price_precision}f}")
            logger.info(f"   ATR: {latest_atr:.{atr_precision}f}")
            logger.info(f"   SuperTrend: ${latest_supertrend:.{st_precision}f}")
            logger.info(f"   Signal: {result['signal_text']}")
            logger.info(f"   Precision used: {price_precision} decimals")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
            
