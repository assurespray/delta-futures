"""
SuperTrend Recovery Indicator — Accelerates band recovery when at a loss.
✅ Uses single-band logic with recovery blending toward price
✅ Uses RMA for ATR calculation
✅ Trailing rule (max/min) still enforced even during recovery

From LuxAlgo Pine Script: When price deviates beyond threshold ATRs from the
switch price (the close at the last trend flip), the band blends toward price
using an EMA-like alpha, tightening faster to recover from bad entries.
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

SIGNAL_UPTREND = 1
SIGNAL_DOWNTREND = -1

class RecoverySuperTrend:
    """
    SuperTrend Recovery indicator.
    
    When the current price is "at a loss" relative to the switch price
    (the close at last trend flip) by more than recovery_threshold * ATR,
    the band blends toward price: alpha * close + (1-alpha) * prevBand.
    The max/min trailing rule is still enforced, so the band can only tighten.
    """
    
    def __init__(
        self, 
        atr_length: int = 10, 
        multiplier: float = 3.0, 
        recovery_alpha: float = 5.0,   # percentage (5.0 = 5%)
        recovery_threshold: float = 1.0,  # in ATR units
        name: str = "RecoveryST"
    ):
        self.atr_length = atr_length
        self.multiplier = multiplier
        self.recovery_alpha = recovery_alpha
        self.recovery_threshold = recovery_threshold
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
        """RMA-based ATR (matches Pine Script ta.atr)."""
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
            
            # Convert percentage alpha to decimal
            alpha = self.recovery_alpha / 100.0
            
            upper_base = hl2 + (self.multiplier * atr)
            lower_base = hl2 - (self.multiplier * atr)
            
            n = len(df)
            st_band = np.zeros(n)
            trend = np.zeros(n, dtype=int)
            is_at_loss = np.zeros(n, dtype=bool)
            switch_price = np.zeros(n)
            
            # Initialize first bar
            st_band[0] = lower_base[0]
            trend[0] = SIGNAL_UPTREND
            switch_price[0] = close_vals[0]
            
            for i in range(1, n):
                deviation = self.recovery_threshold * atr[i]
                
                if trend[i-1] == SIGNAL_UPTREND:
                    # Check if at a loss: price dropped below switch_price by more than threshold
                    is_at_loss[i] = (switch_price[i-1] - close_vals[i]) > deviation
                    
                    if is_at_loss[i]:
                        target_band = alpha * close_vals[i] + (1.0 - alpha) * st_band[i-1]
                    else:
                        target_band = lower_base[i]
                    
                    # Trailing rule: band can only go up in bull trend
                    st_band[i] = max(target_band, st_band[i-1])
                    
                    if close_vals[i] < st_band[i]:
                        # Flip to bear
                        trend[i] = SIGNAL_DOWNTREND
                        st_band[i] = upper_base[i]
                        switch_price[i] = close_vals[i]
                    else:
                        trend[i] = SIGNAL_UPTREND
                        switch_price[i] = switch_price[i-1]
                else:
                    # Check if at a loss: price rose above switch_price by more than threshold
                    is_at_loss[i] = (close_vals[i] - switch_price[i-1]) > deviation
                    
                    if is_at_loss[i]:
                        target_band = alpha * close_vals[i] + (1.0 - alpha) * st_band[i-1]
                    else:
                        target_band = upper_base[i]
                    
                    # Trailing rule: band can only go down in bear trend
                    st_band[i] = min(target_band, st_band[i-1])
                    
                    if close_vals[i] > st_band[i]:
                        # Flip to bull
                        trend[i] = SIGNAL_UPTREND
                        st_band[i] = lower_base[i]
                        switch_price[i] = close_vals[i]
                    else:
                        trend[i] = SIGNAL_DOWNTREND
                        switch_price[i] = switch_price[i-1]

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
                    "is_at_loss": is_at_loss,
                    "switch_price": switch_price,
                    "atr": atr
                }

            latest_idx = -1
            latest_close = float(close_vals[latest_idx])
            latest_st = float(st_band[latest_idx])
            latest_signal = int(trend[latest_idx])
            latest_atr = float(atr[latest_idx])
            latest_at_loss = bool(is_at_loss[latest_idx])
            latest_switch = float(switch_price[latest_idx])
            
            price_precision = self._get_precision(latest_close)
            st_precision = self._get_precision(latest_st)
            atr_precision = self._get_precision(latest_atr)
            
            return {
                "indicator_name": self.name,
                "atr_length": self.atr_length,
                "multiplier": self.multiplier,
                "recovery_alpha": self.recovery_alpha,
                "recovery_threshold": self.recovery_threshold,
                "latest_close": round(latest_close, price_precision),
                "supertrend_value": round(latest_st, st_precision),
                "signal": latest_signal,
                "signal_text": "Uptrend" if latest_signal == SIGNAL_UPTREND else "Downtrend",
                "is_at_loss": latest_at_loss,
                "switch_price": round(latest_switch, price_precision),
                "atr": round(latest_atr, atr_precision),
                "precision": price_precision
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
