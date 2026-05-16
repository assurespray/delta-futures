"""
Donchian Channels Breakout strategy — Turtle Trader Universal Rule.
Conforms to BaseStrategy interface for modular engine execution.

Entry Logic:
- LONG when latest closed candle closes ABOVE the 20-period Upper Channel
- SHORT when latest closed candle closes BELOW the 20-period Lower Channel
- Trigger price = breakout candle high/low +/- 1 pip offset
- If current price already crossed trigger → immediate MARKET order
- Otherwise → pending STOP LIMIT order at trigger price

Exit Logic:
- LONG exit: candle closes below the Middle Band → go FLAT (no reversal)
- SHORT exit: candle closes above the Middle Band → go FLAT (no reversal)
- Bot waits for next Donchian breakout/breakdown to re-enter

Stop-Loss (additional protection):
- Initial SL = Middle Band of the Donchian Channel

Pending Order Invalidation:
- Cancel pending LONG if candle closes below Middle Band
- Cancel pending SHORT if candle closes above Middle Band
"""

import logging
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime
from indicators.donchian import DonchianChannels, SIGNAL_BREAKOUT_UP, SIGNAL_BREAKOUT_DOWN
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles
from config.constants import (
    TIMEFRAME_MAPPING,
    TIMEFRAME_SECONDS,
    CANDLE_CLOSE_BUFFER_SECONDS,
    BREAKOUT_PIP_OFFSET,
)
from utils.timeframe import get_timeframe_seconds
from strategy.base import BaseStrategy, EntrySignal, ExitSignal

logger = logging.getLogger(__name__)


class DonchianBreakoutStrategy(BaseStrategy):
    """Donchian Channels breakout — Turtle Trader universal entry/exit rule."""

    def __init__(self, params: Dict[str, Any] = None):
        self.params = params or {}

        self.period = int(self.params.get("period", 20))

        self.donchian = DonchianChannels(
            period=self.period,
            name="Donchian"
        )

        self._last_fetch_time: Dict[str, datetime] = {}
        self._last_candle_count: Dict[str, int] = {}
        self._last_processed_candle_time: Dict[str, int] = {}

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def _is_candle_closed(self, candles: List[Dict[str, Any]], timeframe: str) -> Dict[str, Any]:
        """Check if latest candle is fully closed + buffer elapsed."""
        if not candles:
            return {'is_closed': False, 'seconds_until_ready': 999, 'reason': 'No candles available'}

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
            'reason': 'Candle closed and buffered' if is_ready else f'Waiting {seconds_until_ready}s'
        }

    async def calculate_indicators(
        self,
        client: DeltaExchangeClient,
        symbol: str,
        timeframe: str,
        skip_boundary_check: bool = False,
        force_recalc: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Fetch candles and calculate Donchian Channels."""
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()

            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"Unknown timeframe: {timeframe}")
                return None

            resolution = TIMEFRAME_MAPPING[timeframe]
            # Donchian needs period + some buffer; fetch extra for safety
            required_candles = max(self.period + 50, 200)
            timeframe_seconds = get_timeframe_seconds(timeframe)

            # Step 1: Quick check — is the latest candle closed?
            latest_candles = await get_candles(client, symbol, timeframe, limit=2)
            if not latest_candles:
                return None

            candle_status = self._is_candle_closed(latest_candles, timeframe)

            # ENFORCE: Do not calculate on incomplete candle data
            if not skip_boundary_check and not candle_status['is_closed']:
                logger.debug(f"Candle not fully closed for {symbol} ({candle_status['reason']}). Skipping calculation.")
                return None

            # Step 2: Fetch full candle history
            end_time = int(datetime.utcnow().timestamp())
            start_time = end_time - int(timeframe_seconds * required_candles * 1.2)
            candles = await get_candles(
                client, symbol, timeframe,
                start_time=start_time, end_time=end_time,
                limit=required_candles
            )

            if not candles:
                logger.error(f"No candles available for {symbol} {timeframe}")
                return None

            actual_count = len(candles)
            latest_candle = candles[-1]
            latest_candle_time = latest_candle.get("time", 0)
            prev_high = float(latest_candle.get("high", 0))
            prev_low = float(latest_candle.get("low", 0))

            # Prevent duplicate processing of same candle
            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed:
                if not force_recalc:
                    return None

            # Recheck candle close on full dataset
            candle_status = self._is_candle_closed(candles, timeframe)
            if not skip_boundary_check and not candle_status['is_closed']:
                logger.debug(f"Candle not fully closed for {symbol} ({candle_status['reason']}). Skipping calculation.")
                return None

            # Minimum data check
            min_required = self.period + 1
            if actual_count < min_required:
                logger.error(f"INSUFFICIENT DATA: got {actual_count}, need at least {min_required}")
                return None

            # Calculate Donchian Channels
            logger.info(f"Calculating Donchian Channels (period={self.period}) for {symbol}")
            dc_result = self.donchian.calculate(candles)
            if not dc_result:
                return None

            logger.info(f"DONCHIAN CALCULATED SUCCESSFULLY")
            logger.info(f"   Upper: ${dc_result['upper']:.5f}, Lower: ${dc_result['lower']:.5f}, Mid: ${dc_result['middle']:.5f}")
            logger.info(f"   Close: ${dc_result['latest_close']:.5f} | Signal: {dc_result['signal_text']}")

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": actual_count,
                "candles_requested": required_candles,
                "candle_status": candle_status,
                "donchian": dc_result,
                # Map to perusu/sirusu for UI compatibility
                "perusu": dc_result,
                "sirusu": dc_result,
                "latest_closed_candle": {"high": prev_high, "low": prev_low},
                "current_price": dc_result.get('latest_close', 0)
            }

            # Mark as processed
            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            self._last_processed_candle_time[cache_key] = latest_candle_time

            return result

        except Exception as e:
            logger.error(f"Exception calculating Donchian indicators: {e}")
            logger.error(traceback.format_exc())
            return None

    def _detect_signal_flip(self, current_signal: int, last_signal: Optional[int]) -> Optional[str]:
        """
        Detect if Donchian signal has flipped from last known state.
        
        Donchian uses 3 states (1, 0, -1), so a "flip" is:
        - From non-bullish to breakout up  -> long
        - From non-bearish to breakout down -> short
        """
        if last_signal is None:
            logger.info(f"Initializing Donchian state: {current_signal} (waiting for flip)")
            return None
        if current_signal == last_signal:
            return None
        if current_signal == SIGNAL_BREAKOUT_UP and last_signal != SIGNAL_BREAKOUT_UP:
            logger.info(f"Donchian FLIP: -> Breakout Up (LONG entry signal)")
            return "long"
        elif current_signal == SIGNAL_BREAKOUT_DOWN and last_signal != SIGNAL_BREAKOUT_DOWN:
            logger.info(f"Donchian FLIP: -> Breakout Down (SHORT entry signal)")
            return "short"
        return None

    def generate_entry_signal(
        self,
        setup_id: str,
        previous_state: Optional[Dict[str, Any]],
        indicators_data: Dict[str, Any]
    ) -> Optional[EntrySignal]:
        """
        Donchian Breakout Entry Rule:
        - LONG: candle closes above Upper Channel → trigger = breakout candle high + 1 pip
        - SHORT: candle closes below Lower Channel → trigger = breakout candle low - 1 pip
        - If price already crossed trigger → MARKET order (immediate)
        - Otherwise → STOP LIMIT order (pending)
        - SL = Middle Band
        """
        try:
            dc = indicators_data.get("donchian")
            previous_candle = indicators_data.get("latest_closed_candle", {})
            current_price = indicators_data.get("current_price")

            if not dc or not previous_candle or not current_price:
                logger.error("Missing Donchian data or candle data for entry signal")
                return None

            prev_high = previous_candle.get("high")
            prev_low = previous_candle.get("low")
            if not prev_high or not prev_low:
                logger.error("Missing latest candle high/low for Donchian")
                return None

            # Extract last signal from previous_state
            last_signal = previous_state.get("primary_signal") if previous_state else None
            current_signal = dc.get("signal")

            entry_side = self._detect_signal_flip(current_signal, last_signal)
            if not entry_side:
                return None

            middle = dc['middle']

            if entry_side == "long":
                trigger_price = prev_high + BREAKOUT_PIP_OFFSET
                # If price already above trigger → immediate market order
                if current_price >= trigger_price:
                    logger.info(f"Donchian LONG: Price ${current_price:.5f} >= trigger ${trigger_price:.5f} → MARKET order (SL: ${middle:.5f})")
                    return EntrySignal(
                        side='long', trigger_price=current_price, stop_loss=middle,
                        immediate=True, reason=f'Donchian Upper Breakout (immediate)'
                    )
            else:
                trigger_price = prev_low - BREAKOUT_PIP_OFFSET
                # If price already below trigger → immediate market order
                if current_price <= trigger_price:
                    logger.info(f"Donchian SHORT: Price ${current_price:.5f} <= trigger ${trigger_price:.5f} → MARKET order (SL: ${middle:.5f})")
                    return EntrySignal(
                        side='short', trigger_price=current_price, stop_loss=middle,
                        immediate=True, reason=f'Donchian Lower Breakout (immediate)'
                    )

            # Price hasn't crossed trigger yet → pending STOP LIMIT order
            logger.info(f"Donchian {entry_side.upper()} pending: trigger ${trigger_price:.5f}, current ${current_price:.5f} (SL: ${middle:.5f})")
            return EntrySignal(
                side=entry_side, trigger_price=trigger_price, stop_loss=middle,
                immediate=False, reason=f"Donchian {'Upper' if entry_side == 'long' else 'Lower'} Breakout (pending)"
            )

        except Exception as e:
            logger.error(f"Exception generating Donchian entry signal: {e}")
            logger.error(traceback.format_exc())
            return None

    def generate_exit_signal(
        self,
        setup_id: str,
        position_side: str,
        indicators_data: Dict[str, Any]
    ) -> Optional[ExitSignal]:
        """
        Turtle Trader Exit Rule:
        - LONG exit: close < Middle Band
        - SHORT exit: close > Middle Band
        """
        try:
            dc = indicators_data.get("donchian")
            if not dc:
                return None

            close = dc.get('latest_close', 0)
            middle = dc.get('middle', 0)

            should_exit = False
            if position_side == "long" and close < middle:
                logger.info(f"Donchian EXIT: Close ${close:.5f} < Middle ${middle:.5f} (Close LONG)")
                should_exit = True
            elif position_side == "short" and close > middle:
                logger.info(f"Donchian EXIT: Close ${close:.5f} > Middle ${middle:.5f} (Close SHORT)")
                should_exit = True

            if should_exit:
                return ExitSignal(
                    reason=f"Price crossed Middle Band ({middle:.5f})",
                    stop_loss=middle
                )

            return None

        except Exception as e:
            logger.error(f"Exception generating Donchian exit signal: {e}")
            return None

    def should_invalidate_pending_entry(self, pending_side: str, indicators_data: Dict[str, Any]) -> bool:
        """
        Cancel pending Donchian entry if price retreats past the Middle Band.
        - Pending LONG invalidated if candle closes below Middle Band
        - Pending SHORT invalidated if candle closes above Middle Band
        """
        dc = indicators_data.get("donchian")
        if not dc:
            return False

        close = dc.get('latest_close', 0)
        middle = dc.get('middle', 0)

        if pending_side == "long" and close < middle:
            logger.info(f"Donchian INVALIDATE pending LONG: close ${close:.5f} < middle ${middle:.5f}")
            return True
        if pending_side == "short" and close > middle:
            logger.info(f"Donchian INVALIDATE pending SHORT: close ${close:.5f} > middle ${middle:.5f}")
            return True
        return False

    def get_cache_mapping(self, indicators_data: Dict[str, Any]) -> Dict[str, Any]:
        """Map Donchian results to IndicatorCache fields for dashboard display."""
        dc = indicators_data.get("donchian", {})
        signal = dc.get("signal", 0)
        # For UI: map breakout signals to 1/-1, inside channel defaults to 1 (neutral/bullish)
        ui_signal = signal if signal != 0 else 1
        upper = dc.get("upper", 0.0)
        lower = dc.get("lower", 0.0)
        middle = dc.get("middle", 0.0)
        signal_text = dc.get("signal_text", "Inside Channel")
        return {
            "current_price": indicators_data.get("current_price", 0.0),
            "primary_name": f"Donchian({self.period})",
            "primary_signal": ui_signal,
            "primary_signal_text": signal_text,
            "primary_value": upper,
            "secondary_name": "DC Middle",
            "secondary_signal": ui_signal,
            "secondary_signal_text": signal_text,
            "secondary_value": middle,
            "strategy_state": {
                "primary_signal": signal,
            },
            "display_details": {
                "Signal": signal_text,
                "Upper": upper,
                "Lower": lower,
                "Middle (SL)": middle,
            }
        }
