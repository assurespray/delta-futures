"""
LazyBear Range Breakout strategy.
Conforms to BaseStrategy interface for modular engine execution.

Entry Logic:
- Range breakout signal (1 for long, -1 for short) triggers IMMEDIATE market entry
- No flip detection needed — breakout itself is the signal
- SL from range boundaries (middle or opposite)

Exit Logic:
- Price crosses EMA triggers market exit
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from indicators.range_identifier import RangeIdentifierLazyBear
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles
from config.constants import TIMEFRAME_MAPPING, TIMEFRAME_SECONDS, CANDLE_CLOSE_BUFFER_SECONDS
from utils.timeframe import get_timeframe_seconds
from strategy.base import BaseStrategy, EntrySignal, ExitSignal

logger = logging.getLogger(__name__)


class RangeBreakoutStrategy(BaseStrategy):
    """LazyBear Range Breakout — always uses market orders, no flip detection."""

    def __init__(self, params: Dict[str, Any] = None):
        self.params = params or {}

        self.ema_length = int(self.params.get("ema_length", 34))
        self.min_range_candles = int(self.params.get("min_range_candles", 2))
        self.sl_type = self.params.get("sl_type", "middle")  # "middle" or "opposite"

        self.indicator = RangeIdentifierLazyBear(
            ema_length=self.ema_length,
            min_range_candles=self.min_range_candles
        )

        self._last_fetch_time: Dict[str, datetime] = {}
        self._last_candle_count: Dict[str, int] = {}
        self._last_processed_candle_time: Dict[str, int] = {}

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def _is_candle_closed(self, candles: List[Dict[str, Any]], timeframe: str) -> Dict[str, Any]:
        if not candles:
            return {'is_closed': False, 'seconds_until_ready': 999, 'reason': 'No candles'}

        latest_candle = candles[-1]
        candle_time = latest_candle.get("time", 0)
        current_time = int(datetime.utcnow().timestamp())

        timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 180)
        candle_close_time = candle_time + timeframe_seconds

        buffer_seconds = CANDLE_CLOSE_BUFFER_SECONDS
        ready_time = candle_close_time + buffer_seconds

        is_ready = current_time >= ready_time
        seconds_until_ready = max(0, ready_time - current_time)

        return {
            'is_closed': is_ready,
            'seconds_until_ready': seconds_until_ready,
            'reason': 'Ready' if is_ready else f'Waiting {seconds_until_ready}s'
        }

    async def calculate_indicators(self, client: DeltaExchangeClient, symbol: str, timeframe: str, skip_boundary_check: bool = False, force_recalc: bool = False) -> Optional[Dict[str, Any]]:
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()

            if timeframe not in TIMEFRAME_MAPPING:
                return None

            resolution = TIMEFRAME_MAPPING[timeframe]
            required_candles = self.ema_length + 250
            timeframe_seconds = get_timeframe_seconds(timeframe)

            latest_candles = await get_candles(client, symbol, timeframe, limit=2)
            if not latest_candles:
                return None

            candle_status = self._is_candle_closed(latest_candles, timeframe)

            end_time = int(current_time.timestamp())
            start_time = end_time - int(timeframe_seconds * required_candles * 1.2)
            candles = await get_candles(client, symbol, timeframe, start_time=start_time, end_time=end_time, limit=required_candles)

            if not candles:
                return None

            actual_count = len(candles)
            latest_candle = candles[-1]
            latest_candle_time = latest_candle.get("time", 0)

            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed and not force_recalc:
                return None

            result_data = self.indicator.calculate(candles)
            if not result_data:
                return None

            # Add supertrend_value mapping to prevent KeyErrors in Cache
            result_data["supertrend_value"] = result_data["ema"]

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": actual_count,
                "candle_status": candle_status,
                "range_data": result_data,
                # Map for tracker
                "perusu": result_data,
                "sirusu": result_data,
                "current_price": result_data.get('latest_close', 0)
            }

            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            self._last_processed_candle_time[cache_key] = latest_candle_time

            return result

        except Exception as e:
            logger.error(f"Error calculating RangeBreakoutStrategy: {e}")
            return None

    def generate_entry_signal(self, setup_id: str, previous_state: Optional[Dict[str, Any]], indicators_data: Dict[str, Any]) -> Optional[EntrySignal]:
        """Range breakout ignores previous_state — breakout signal itself is the trigger."""
        try:
            data = indicators_data.get("range_data")
            if not data:
                return None

            signal = data.get("signal", 0)
            if signal == 0:
                return None

            current_price = data.get("latest_close")

            # Determine SL based on broken range
            prev_up = data.get("prev_up")
            prev_down = data.get("prev_down")

            if signal == 1:
                sl = (prev_up + prev_down) / 2 if self.sl_type == "middle" else prev_down
                return EntrySignal(
                    side='long',
                    trigger_price=current_price,
                    stop_loss=sl,
                    immediate=True,
                    reason=f'LazyBear Long Breakout (SL: {self.sl_type})'
                )
            elif signal == -1:
                sl = (prev_up + prev_down) / 2 if self.sl_type == "middle" else prev_up
                return EntrySignal(
                    side='short',
                    trigger_price=current_price,
                    stop_loss=sl,
                    immediate=True,
                    reason=f'LazyBear Short Breakout (SL: {self.sl_type})'
                )

            return None
        except Exception as e:
            logger.error(f"Error generating entry signal: {e}")
            return None

    def generate_exit_signal(self, setup_id: str, position_side: str, indicators_data: Dict[str, Any]) -> Optional[ExitSignal]:
        """Exit when price crosses EMA."""
        try:
            data = indicators_data.get("range_data")
            if not data:
                return None

            close = data.get("latest_close")
            ema = data.get("ema")

            should_exit = False
            if position_side == "long" and close < ema:
                should_exit = True
            elif position_side == "short" and close > ema:
                should_exit = True

            if should_exit:
                return ExitSignal(
                    reason=f"Price crossed EMA ({ema:.2f})",
                    stop_loss=ema
                )

            return None
        except Exception as e:
            return None

    def should_invalidate_pending_entry(self, pending_side: str, indicators_data: Dict[str, Any]) -> bool:
        """Range breakout always uses immediate market orders, so no pending invalidation needed."""
        return False

    def get_cache_mapping(self, indicators_data: Dict[str, Any]) -> Dict[str, Any]:
        """Map Range Breakout results to IndicatorCache fields."""
        data = indicators_data.get("range_data", {})
        signal = data.get("signal", 0)
        ema = data.get("ema", 0.0)
        return {
            "current_price": indicators_data.get("current_price", 0.0),
            "perusu_signal": signal if signal != 0 else 1,
            "perusu_signal_text": "Uptrend" if signal >= 0 else "Downtrend",
            "perusu_value": ema,
            "sirusu_signal": signal if signal != 0 else 1,
            "sirusu_signal_text": "Uptrend" if signal >= 0 else "Downtrend",
            "sirusu_value": ema,
            "strategy_state": {}
        }
