"""
Donchian Channels Indicator — Turtle Trader Universal Rule.

Calculates the 20-period (configurable) highest high and lowest low
from CLOSED candles only (excludes the current open candle to prevent repainting).

Upper Channel = Highest High of last N closed candles
Lower Channel = Lowest Low of last N closed candles
Middle Band   = (Upper + Lower) / 2

Signal:
  1  = Close > Upper Channel (breakout up — bullish)
 -1  = Close < Lower Channel (breakout down — bearish)
  0  = Inside channel (no breakout)
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

SIGNAL_BREAKOUT_UP = 1
SIGNAL_BREAKOUT_DOWN = -1
SIGNAL_INSIDE = 0


class DonchianChannels:
    """
    Donchian Channels indicator.

    Uses only CLOSED candles (excludes the last/current candle) to prevent
    repainting — the channel boundaries are final once the candle closes.

    Parameters:
        period: Lookback period for highest high / lowest low (default 20)
        name: Indicator name for logging
    """

    def __init__(self, period: int = 20, name: str = "Donchian"):
        self.period = period
        self.name = name

    @staticmethod
    def _get_precision(value: float) -> int:
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

    def calculate(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Calculate Donchian Channels from candle data.

        Requires at least (period + 1) candles:
          - The last candle is treated as the CURRENT (open/unfinished) candle
          - The preceding `period` candles form the lookback window

        Returns:
            Dict with upper, lower, middle, signal, latest_close, etc.
            None if insufficient data.
        """
        try:
            min_required = self.period + 1
            if not candles or len(candles) < min_required:
                logger.warning(
                    f"Insufficient data for {self.name}: "
                    f"need {min_required}, got {len(candles) if candles else 0}"
                )
                return None

            # Exclude the current (potentially open) candle
            closed_candles = candles[:-1]

            # Lookback window = last `period` CLOSED candles
            window = closed_candles[-self.period:]

            upper = max(float(c['high']) for c in window)
            lower = min(float(c['low']) for c in window)
            middle = (upper + lower) / 2.0

            # Latest CLOSED candle's close price (the one we just confirmed)
            latest_close = float(closed_candles[-1]['close'])

            # Determine signal
            if latest_close > upper:
                signal = SIGNAL_BREAKOUT_UP
                signal_text = "Breakout Up"
            elif latest_close < lower:
                signal = SIGNAL_BREAKOUT_DOWN
                signal_text = "Breakout Down"
            else:
                signal = SIGNAL_INSIDE
                signal_text = "Inside Channel"

            # Precision
            price_precision = self._get_precision(latest_close)
            channel_precision = self._get_precision(upper)

            result = {
                "indicator_name": self.name,
                "period": self.period,
                "upper": round(upper, channel_precision),
                "lower": round(lower, channel_precision),
                "middle": round(middle, channel_precision),
                "latest_close": round(latest_close, price_precision),
                "signal": signal,
                "signal_text": signal_text,
                # Map supertrend_value to middle band for cache compatibility
                "supertrend_value": round(middle, channel_precision),
                "precision": price_precision,
            }

            logger.info(
                f"{self.name}({self.period}) | "
                f"Upper: {result['upper']}, Lower: {result['lower']}, "
                f"Mid: {result['middle']} | Close: {result['latest_close']} | "
                f"{signal_text}"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
