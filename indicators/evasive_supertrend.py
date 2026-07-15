"""
Evasive SuperTrend Indicator - Adapts based on noise to avoid false flips.
✅ Uses single-band logic with dynamic noise avoidance
✅ Uses RMA for ATR calculation
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

SIGNAL_UPTREND = 1
SIGNAL_DOWNTREND = -1

class EvasiveSuperTrend:
    """
    Evasive SuperTrend indicator.
    
    When price gets too close to the trailing band (within noise_threshold * ATR),
    the band expands outwards (by expansion_alpha * ATR) instead of tightening,
    avoiding false signal flips in choppy markets.
    """
    
    def __init__(
        self, 
        atr_length: int = 10, 
        multiplier: float = 3.0, 
        noise_threshold: float = 1.0, 
        expansion_alpha: float = 0.5,
        name: str = "EvasiveST"
    ):
        self.atr_length = atr_length
        self.multiplier = multiplier
        self.noise_threshold = noise_threshold
        self.expansion_alpha = expansion_alpha
        self.name = name

    @staticmethod
    def candles_to_dataframe(candles: List[Dict[str, Any]]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.reset_index(drop=True)
    
    @staticmethod
    def _get_precision(value: float) -> int:
        if value == 0: return 8
        abs_value = abs(value)
        if abs_value < 0.0001: return 8
        elif abs_value < 1: return 6
        elif abs_value < 100: return 4
        else: return 2

    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close)
            )
        )
        
        atr = np.zeros(len(df))
        alpha = 1.0 / self.atr_length
        
        if len(df) >= self.atr_length:
            atr[self.atr_length - 1] = np.mean(tr[:self.atr_length])
        
        for i in range(self.atr_length, len(df)):
            atr[i] = atr[i-1] * (1 - alpha) + tr[i] * alpha
            
        atr_series = pd.Series(atr, index=df.index)
        atr_series = atr_series.replace(0, np.nan).ffill().bfill()
        return atr_series

    def calculate(self, candles: List[Dict[str, Any]], return_series: bool = False) -> Optional[Dict[str, Any]]:
        try:
            df = self.candles_to_dataframe(candles)
            if len(df) < self.atr_length + 1:
                return None
                
            atr = self.calculate_atr(df).values
            hl2 = (df['high'].values + df['low'].values) / 2.0
            close_vals = df['close'].values
            
            upper_base = hl2 + (self.multiplier * atr)
            lower_base = hl2 - (self.multiplier * atr)
            
            n = len(df)
            st_band = np.zeros(n)
            trend = np.zeros(n, dtype=int)
            is_noisy = np.zeros(n, dtype=bool)
            
            # Initialize first value
            st_band[0] = lower_base[0]
            trend[0] = SIGNAL_UPTREND
            
            for i in range(1, n):
                is_noisy[i] = abs(close_vals[i] - st_band[i-1]) < (atr[i] * self.noise_threshold)
                
                if trend[i-1] == SIGNAL_UPTREND:
                    if is_noisy[i]:
                        st_band[i] = st_band[i-1] - (atr[i] * self.expansion_alpha)
                    else:
                        st_band[i] = max(lower_base[i], st_band[i-1])
                        
                    if close_vals[i] < st_band[i]:
                        trend[i] = SIGNAL_DOWNTREND
                        st_band[i] = upper_base[i]
                    else:
                        trend[i] = SIGNAL_UPTREND
                else:
                    if is_noisy[i]:
                        st_band[i] = st_band[i-1] + (atr[i] * self.expansion_alpha)
                    else:
                        st_band[i] = min(upper_base[i], st_band[i-1])
                        
                    if close_vals[i] > st_band[i]:
                        trend[i] = SIGNAL_UPTREND
                        st_band[i] = lower_base[i]
                    else:
                        trend[i] = SIGNAL_DOWNTREND

            if return_series:
                return {
                    "time": [c["time"] for c in candles],
                    "open": df['open'].values,
                    "high": df['high'].values,
                    "low": df['low'].values,
                    "close": df['close'].values,
                    "volume": df['volume'].values if 'volume' in df.columns else np.zeros(n),
                    "supertrend": st_band,
                    "signal": trend,
                    "is_noisy": is_noisy,
                    "atr": atr
                }

            latest_idx = -1
            latest_close = float(close_vals[latest_idx])
            latest_st = float(st_band[latest_idx])
            latest_signal = int(trend[latest_idx])
            latest_atr = float(atr[latest_idx])
            latest_noisy = bool(is_noisy[latest_idx])
            
            price_precision = self._get_precision(latest_close)
            st_precision = self._get_precision(latest_st)
            atr_precision = self._get_precision(latest_atr)
            
            return {
                "indicator_name": self.name,
                "atr_length": self.atr_length,
                "multiplier": self.multiplier,
                "noise_threshold": self.noise_threshold,
                "expansion_alpha": self.expansion_alpha,
                "latest_close": round(latest_close, price_precision),
                "supertrend_value": round(latest_st, st_precision),
                "signal": latest_signal,
                "signal_text": "Uptrend" if latest_signal == SIGNAL_UPTREND else "Downtrend",
                "is_noisy": latest_noisy,
                "atr": round(latest_atr, atr_precision),
                "precision": price_precision
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
