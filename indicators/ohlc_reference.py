"""
OHLC Reference Candle Indicator — Breakout Level Extractor.

Extracts High/Low from a reference timeframe candle to serve as
breakout target levels. Optionally merges with the previous candle
for wider breakout zones.

Returns:
  target_high = Reference candle high (or max of last 2 candles if use_prev_candle)
  target_low  = Reference candle low  (or min of last 2 candles if use_prev_candle)
  ref_mid     = (target_high + target_low) / 2

Usage:
  indicator = OHLCReference(use_prev_candle=True)
  result = indicator.calculate(reference_timeframe_candles)
  # result["target_high"], result["target_low"] → breakout levels
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class OHLCReference:
    """
    Pure math indicator that extracts breakout levels from reference candles.

    Parameters:
        use_prev_candle: If True, merge last 2 closed candles:
                         target_high = max(candle[-1].high, candle[-2].high)
                         target_low  = min(candle[-1].low,  candle[-2].low)
        name: Indicator name for logging.
    """

    def __init__(self, use_prev_candle: bool = False, name: str = "OHLC Ref"):
        self.use_prev_candle = use_prev_candle
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
        Calculate target high/low from reference timeframe candles.

        The caller must pass CLOSED candles only. candles[-1] is the most
        recent fully closed reference candle.

        Args:
            candles: List of OHLC dicts from the reference timeframe.
                     Needs >= 2 candles (>= 3 if use_prev_candle=True).

        Returns:
            Dict with target_high, target_low, ref_mid, etc., or None.
        """
        try:
            min_required = 3 if self.use_prev_candle else 2
            if not candles or len(candles) < min_required:
                logger.warning(
                    f"Insufficient data for {self.name}: "
                    f"need {min_required}, got {len(candles) if candles else 0}"
                )
                return None

            ref_candle = candles[-1]
            ref_high = float(ref_candle['high'])
            ref_low = float(ref_candle['low'])

            if self.use_prev_candle:
                prev_candle = candles[-2]
                ref_high = max(ref_high, float(prev_candle['high']))
                ref_low = min(ref_low, float(prev_candle['low']))

            ref_mid = (ref_high + ref_low) / 2.0
            precision = self._get_precision(ref_high)

            result = {
                "indicator_name": self.name,
                "target_high": round(ref_high, precision),
                "target_low": round(ref_low, precision),
                "ref_mid": round(ref_mid, precision),
                "ref_open": round(float(ref_candle['open']), precision),
                "ref_close": round(float(ref_candle['close']), precision),
                "ref_time": ref_candle.get("time", 0),
                "use_prev_candle": self.use_prev_candle,
                "precision": precision,
            }

            logger.info(
                f"{self.name} | High: {result['target_high']}, "
                f"Low: {result['target_low']}, Mid: {result['ref_mid']} | "
                f"Merge: {self.use_prev_candle}"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to calculate {self.name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
