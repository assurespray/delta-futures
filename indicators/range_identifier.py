import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class RangeIdentifierLazyBear:
    """
    LazyBear Range Identifier with Stateless Trend & Breakout Logic.
    Calculates the Up/Down range, Midline, and EMA.
    Tracks whether a breakout is the FIRST in a trend phase.
    """
    def __init__(self, ema_length: int = 34, min_range_candles: int = 2):
        self.ema_length = ema_length
        self.min_range_candles = min_range_candles

    def calculate(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candles or len(candles) < self.ema_length + 5:
            logger.error(f"Not enough candles for RangeIdentifier. Need {self.ema_length + 5}, got {len(candles) if candles else 0}")
            return None
            
        try:
            df = pd.DataFrame(candles)
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            
            # Calculate EMA
            df['ema'] = df['close'].ewm(span=self.ema_length, adjust=False).mean()
            
            up = np.zeros(len(df))
            down = np.zeros(len(df))
            range_count = np.zeros(len(df))
            
            closes = df['close'].values
            highs = df['high'].values
            lows = df['low'].values
            emas = df['ema'].values
            
            up[0] = highs[0]
            down[0] = lows[0]
            range_count[0] = 1
            
            trend_phase = 0 # 1 for Long, -1 for Short
            has_traded_this_trend = False
            
            signals = [0]
            
            for i in range(1, len(df)):
                c = closes[i]
                h = highs[i]
                l = lows[i]
                
                # Calculate Range
                if c < up[i-1] and c > down[i-1]:
                    up[i] = up[i-1]
                    down[i] = down[i-1]
                    range_count[i] = range_count[i-1] + 1
                else:
                    up[i] = h
                    down[i] = l
                    range_count[i] = 1
                    
                # Determine Trend Phase
                curr_trend = 1 if c > emas[i] else (-1 if c < emas[i] else trend_phase)
                
                if curr_trend != trend_phase:
                    # Trend changed! Reset the trade flag
                    trend_phase = curr_trend
                    has_traded_this_trend = False
                    
                # Check for breakout signals
                signal = 0
                if not has_traded_this_trend:
                    # Breakout happens when range_count resets to 1 AND previous range count >= min_range_candles
                    if range_count[i] == 1 and range_count[i-1] >= self.min_range_candles:
                        if c > up[i-1] and trend_phase == 1:
                            signal = 1
                            has_traded_this_trend = True
                        elif c < down[i-1] and trend_phase == -1:
                            signal = -1
                            has_traded_this_trend = True
                            
                signals.append(signal)
            
            df['up'] = up
            df['down'] = down
            df['range_count'] = range_count
            df['signal'] = signals
            df['mid'] = (df['up'] + df['down']) / 2
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            signal_val = int(latest['signal'])
            signal_text = "Neutral"
            if signal_val == 1: signal_text = "Long Breakout"
            elif signal_val == -1: signal_text = "Short Breakout"
            
            return {
                "up": float(latest['up']),
                "down": float(latest['down']),
                "mid": float(latest['mid']),
                "ema": float(latest['ema']),
                "signal": signal_val,
                "signal_text": signal_text,
                "prev_up": float(prev['up']),
                "prev_down": float(prev['down']),
                "range_count": int(latest['range_count']),
                "trend_phase": 1 if float(latest['close']) > float(latest['ema']) else -1,
                "latest_close": float(latest['close'])
            }
            
        except Exception as e:
            logger.error(f"Error calculating RangeIdentifier: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
