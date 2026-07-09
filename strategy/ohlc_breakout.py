"""
OHLC Breakout strategy — Reference Candle Breakout System.
Conforms to BaseStrategy interface for modular engine execution.

Core Concept:
- Extracts High/Low from a reference timeframe candle at a specific IST time.
- These levels become breakout targets for the trading timeframe.
- Supports merging with the previous reference candle (use_prev_candle).

Entry Modes:
- "breakout": pending stop-market order at target ± pip_offset (intra-candle).
- "confirmation": waits for trading TF candle close above/below target, then market.

Stop Loss:
- "opposite": SL at the opposite target boundary.
- "middle": SL at (target_high + target_low) / 2.

Take Profit:
- Computed from RR ratio: entry ± (risk × rr_ratio).

Exit Logic:
- TP and SL are the only exits (no signal-based exit).
- Levels remain active until the next reference candle forms.
"""

import logging
import numpy as np
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from indicators.ohlc_reference import OHLCReference
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


class OHLCBreakoutStrategy(BaseStrategy):
    """OHLC Reference Candle Breakout — daily level extraction and breakout entry."""

    def __init__(self, params: Dict[str, Any] = None):
        self.params = params or {}

        # Strategy parameters
        self.reference_time = str(self.params.get("reference_time", "09:15"))
        self.reference_timeframe = str(self.params.get("reference_timeframe", "1h"))
        self.use_prev_candle = bool(self.params.get("use_prev_candle", False))
        self.sl_type = str(self.params.get("sl_type", "opposite"))     # "opposite" | "middle"
        self.rr_ratio = float(self.params.get("rr_ratio", 2.0))
        self.pip_offset_multiplier = float(self.params.get("pip_offset_multiplier", 1.0))
        self.entry_mode = str(self.params.get("entry_mode", "confirmation"))  # "breakout" | "confirmation"

        # Parse reference time
        parts = self.reference_time.split(":")
        self.ref_hour = int(parts[0])
        self.ref_minute = int(parts[1]) if len(parts) > 1 else 0

        # Indicator
        self.indicator = OHLCReference(
            use_prev_candle=self.use_prev_candle,
            name="OHLC Ref"
        )

        # Confirmation mode uses transient signals (breakout candle = trigger)
        if self.entry_mode == "confirmation":
            self.uses_transient_signals = True

        # Instance state for TP tracking (live engine only)
        self._tp_price = 0.0
        self._pending_ref_time = 0  # ref_time when pending order was created

        # Dedup tracking
        self._last_processed_candle_time: Dict[str, int] = {}

    # ======================================================================
    # BACKTEST — Vectorized signal generation
    # ======================================================================

    def generate_backtest_signals(self, df):
        """
        Vectorized signal generation for the backtester.

        Resamples trading TF data to the reference timeframe by tracking
        candles within the daily reference window. Forward-fills target
        levels after each reference window closes.

        Returns standard signal dict + rr_ratio for engine TP computation.
        """
        from utils.time_utils import IST
        from utils.market_utils import get_tick_size
        
        symbol = self.params.get("symbol", "")
        tick_size = get_tick_size(symbol) if symbol else 0.0001
        actual_pip_offset = self.pip_offset_multiplier * tick_size

        n = len(df)
        times = df['time'].astype(int).values
        highs = df['high'].astype(float).values
        lows = df['low'].astype(float).values
        closes = df['close'].astype(float).values

        ref_tf_seconds = TIMEFRAME_SECONDS.get(self.reference_timeframe, 3600)

        # Output arrays
        entry_signal = np.zeros(n, dtype=int)
        sl_price_long = np.zeros(n)
        sl_price_short = np.zeros(n)
        exit_long = np.full(n, False)
        exit_short = np.full(n, False)
        indicator_value = np.zeros(n)

        # Reference window state
        active_high = 0.0
        active_low = 0.0
        prev_ref_high = 0.0
        prev_ref_low = 0.0
        current_ref_start_ts = 0
        building_high = 0.0
        building_low = float('inf')
        in_ref_window = False
        targets_valid = False

        for i in range(n):
            ts = int(times[i])
            dt = datetime.fromtimestamp(ts, tz=IST)

            # Compute the most recent reference window start
            today_ref_start = dt.replace(
                hour=self.ref_hour, minute=self.ref_minute,
                second=0, microsecond=0
            )
            today_ref_start_ts = int(today_ref_start.timestamp())

            # If candle is before today's ref start, use yesterday's
            if ts < today_ref_start_ts:
                nearest_ref_start_ts = today_ref_start_ts - 86400
            else:
                nearest_ref_start_ts = today_ref_start_ts

            nearest_ref_end_ts = nearest_ref_start_ts + ref_tf_seconds

            # Check if candle is within a reference window
            candle_in_window = nearest_ref_start_ts <= ts < nearest_ref_end_ts

            if candle_in_window:
                if current_ref_start_ts != nearest_ref_start_ts:
                    # New reference window starting
                    if in_ref_window and building_high > 0:
                        # Finalize previous window (edge case: back-to-back windows)
                        self._finalize_ref_targets(
                            building_high, building_low,
                            prev_ref_high, prev_ref_low
                        )

                    current_ref_start_ts = nearest_ref_start_ts
                    in_ref_window = True
                    building_high = highs[i]
                    building_low = lows[i]
                else:
                    # Continue building
                    building_high = max(building_high, highs[i])
                    building_low = min(building_low, lows[i])
            else:
                if in_ref_window:
                    # Just exited the reference window — finalize targets
                    in_ref_window = False

                    if self.use_prev_candle and prev_ref_high > 0:
                        active_high = max(building_high, prev_ref_high)
                        active_low = min(building_low, prev_ref_low)
                    else:
                        active_high = building_high
                        active_low = building_low

                    prev_ref_high = building_high
                    prev_ref_low = building_low
                    targets_valid = True

            # Store ref mid as indicator value
            if active_high > 0 and active_low > 0 and active_low < float('inf'):
                ref_mid = (active_high + active_low) / 2
            else:
                ref_mid = 0
            indicator_value[i] = ref_mid

            # Don't generate signals during ref window or before targets are valid
            if in_ref_window or not targets_valid:
                continue

            # ---- Entry signal generation ----
            if self.entry_mode == "confirmation":
                # Candle close above target_high → long
                if closes[i] > active_high:
                    entry_signal[i] = 1
                # Candle close below target_low → short
                elif closes[i] < active_low:
                    entry_signal[i] = -1
            else:
                # Breakout mode: high/low pierce target ± actual_pip_offset
                if highs[i] > active_high + actual_pip_offset:
                    entry_signal[i] = 1
                # Short breakout (long takes priority if both trigger)
                if entry_signal[i] == 0 and lows[i] < active_low - actual_pip_offset:
                    entry_signal[i] = -1

            # ---- SL prices ----
            if entry_signal[i] == 1:
                sl_price_long[i] = active_low if self.sl_type == "opposite" else ref_mid
            elif entry_signal[i] == -1:
                sl_price_short[i] = active_high if self.sl_type == "opposite" else ref_mid

        # Forward-fill SL values (engine reads sl_price_*[i-1] on entry at candle i)
        for i in range(1, n):
            if sl_price_long[i] == 0 and sl_price_long[i - 1] != 0:
                sl_price_long[i] = sl_price_long[i - 1]
            if sl_price_short[i] == 0 and sl_price_short[i - 1] != 0:
                sl_price_short[i] = sl_price_short[i - 1]

        return {
            "entry_signal": entry_signal,
            "exit_long": exit_long,        # All False — exits are SL/TP only
            "exit_short": exit_short,      # All False — exits are SL/TP only
            "sl_price_long": sl_price_long,
            "sl_price_short": sl_price_short,
            "indicator_value": indicator_value,
            "rr_ratio": self.rr_ratio,     # Engine computes TP from entry price + SL + this
        }

    def _finalize_ref_targets(self, bld_high, bld_low, prev_high, prev_low):
        """Helper for edge case finalization (not normally needed in backtest loop)."""
        pass

    # ======================================================================
    # LIVE — Indicator calculation
    # ======================================================================

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def _is_candle_closed(self, candles: List[Dict[str, Any]], timeframe: str) -> Dict[str, Any]:
        """Check if latest candle is fully closed + buffer elapsed."""
        if not candles:
            return {'is_closed': False, 'seconds_until_ready': 999, 'reason': 'No candles'}

        latest_candle = candles[-1]
        candle_time = latest_candle.get("time", 0)
        current_time = int(datetime.utcnow().timestamp())

        timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 180)
        candle_close_time = candle_time + timeframe_seconds
        ready_time = candle_close_time + CANDLE_CLOSE_BUFFER_SECONDS

        is_ready = current_time >= ready_time
        seconds_until_ready = max(0, ready_time - current_time)

        return {
            'is_closed': is_ready,
            'seconds_until_ready': seconds_until_ready,
            'reason': 'Ready' if is_ready else f'Waiting {seconds_until_ready}s'
        }

    async def calculate_indicators(
        self,
        client: DeltaExchangeClient,
        symbol: str,
        timeframe: str,
        skip_boundary_check: bool = False,
        force_recalc: bool = False,
        historical_candles: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch trading TF candles (for price) and reference TF candles (for levels).

        The reference TF candles are filtered to find those matching reference_time
        in IST. The OHLCReference indicator extracts target high/low from them.
        """
        try:
            from utils.time_utils import IST

            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()

            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"Unknown timeframe: {timeframe}")
                return None

            resolution = TIMEFRAME_MAPPING[timeframe]

            # --- Step 1: Check trading TF candle close ---
            if historical_candles is not None:
                trading_candles = historical_candles
                candle_status = {'is_closed': True, 'reason': 'Historical'}
            else:
                latest_candles = await get_candles(client, symbol, timeframe, limit=2)
                if not latest_candles:
                    return None

                candle_status = self._is_candle_closed(latest_candles, timeframe)
                if not skip_boundary_check and not candle_status['is_closed']:
                    logger.debug(f"Trading candle not closed ({candle_status['reason']}). Skipping.")
                    return None

                # Fetch more trading candles for price data
                tf_seconds = get_timeframe_seconds(timeframe)
                end_time = int(current_time.timestamp())
                start_time = end_time - int(tf_seconds * 200 * 1.2)
                trading_candles = await get_candles(
                    client, symbol, timeframe,
                    start_time=start_time, end_time=end_time, limit=200
                )

            if not trading_candles:
                logger.error(f"No trading candles for {symbol} {timeframe}")
                return None

            latest_candle = trading_candles[-1]
            latest_candle_time = latest_candle.get("time", 0)

            # Dedup check
            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed:
                if not force_recalc:
                    return None

            # --- Step 2: Fetch reference TF candles ---
            if self.reference_timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"Unknown reference timeframe: {self.reference_timeframe}")
                return None

            ref_tf_seconds = TIMEFRAME_SECONDS.get(self.reference_timeframe, 3600)
            # Fetch enough reference candles to find recent ones at reference_time
            # 50 candles at 1h = ~2 days, at 4h = ~8 days — plenty
            ref_candles = await get_candles(client, symbol, self.reference_timeframe, limit=50)
            if not ref_candles:
                logger.error(f"No reference candles for {symbol} {self.reference_timeframe}")
                return None

            # --- Step 3: Filter to candles matching reference_time (IST) ---
            now_ts = int(current_time.timestamp())
            matching_candles = []
            for c in ref_candles:
                c_time = c.get("time", 0)
                dt = datetime.fromtimestamp(c_time, tz=IST)
                # Match hour:minute in IST
                if dt.hour == self.ref_hour and dt.minute == self.ref_minute:
                    # Only use fully closed candles
                    if c_time + ref_tf_seconds <= now_ts:
                        matching_candles.append(c)

            if not matching_candles:
                logger.warning(
                    f"No closed reference candles found at {self.reference_time} IST "
                    f"for {symbol} ({self.reference_timeframe})"
                )
                return None

            # --- Step 4: Calculate reference levels ---
            ref_result = self.indicator.calculate(matching_candles)
            if not ref_result:
                return None

            current_price = float(latest_candle.get('close', 0))
            target_high = ref_result['target_high']
            target_low = ref_result['target_low']
            ref_mid = ref_result['ref_mid']

            # Determine status
            if current_price > target_high:
                status = "High Broken"
            elif current_price < target_low:
                status = "Low Broken"
            else:
                status = "Waiting"

            logger.info(
                f"OHLC REF | {symbol} | High: {target_high}, Low: {target_low}, "
                f"Mid: {ref_mid} | Price: {current_price} | {status}"
            )

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": len(trading_candles),
                "candle_status": candle_status,
                "ohlc_ref": ref_result,
                "current_price": current_price,
                "status": status,
                # Compatibility mappings
                "perusu": ref_result,
                "sirusu": ref_result,
                # Include TP for exit checks
                "_tp_price": self._tp_price,
            }

            self._last_processed_candle_time[cache_key] = latest_candle_time

            return result

        except Exception as e:
            logger.error(f"Exception calculating OHLC Breakout indicators: {e}")
            logger.error(traceback.format_exc())
            return None

    # ======================================================================
    # LIVE — Entry signal
    # ======================================================================

    def generate_entry_signal(
        self,
        setup_id: str,
        previous_state: Optional[Dict[str, Any]],
        indicators_data: Dict[str, Any]
    ) -> Optional[EntrySignal]:
        """
        OHLC Breakout Entry:
        - Confirmation mode: candle close above target_high (long) or below target_low (short).
        - Breakout mode: pending stop-market at target ± pip_offset.

        SL = opposite boundary or ref_mid (based on sl_type).
        TP computed and stored for exit signal checking.
        """
        try:
            ref = indicators_data.get("ohlc_ref")
            if not ref:
                logger.error("Missing OHLC reference data for entry signal")
                return None

            target_high = ref['target_high']
            target_low = ref['target_low']
            ref_mid = ref['ref_mid']
            ref_time = ref.get('ref_time', 0)
            current_price = indicators_data.get("current_price", 0)

            if not target_high or not target_low or not current_price:
                return None

            # Reset TP tracking when no position
            self._tp_price = 0.0
            
            # Dynamic tick size calculation
            from utils.market_utils import get_tick_size
            symbol = indicators_data.get("symbol", "")
            tick_size = get_tick_size(symbol) if symbol else 0.0001
            actual_pip_offset = self.pip_offset_multiplier * tick_size

            # Check if reference candle changed since last entry
            last_ref_time = previous_state.get("ref_time", 0) if previous_state else 0

            if self.entry_mode == "confirmation":
                # --- Confirmation mode: candle close triggers ---
                if current_price > target_high:
                    sl = target_low if self.sl_type == "opposite" else ref_mid
                    risk = current_price - sl
                    self._tp_price = current_price + risk * self.rr_ratio
                    self._pending_ref_time = ref_time

                    logger.info(
                        f"OHLC LONG (confirmed): Price {current_price} > Target {target_high} | "
                        f"SL: {sl} | TP: {self._tp_price:.5f}"
                    )
                    return EntrySignal(
                        side='long',
                        trigger_price=current_price,
                        stop_loss=sl,
                        immediate=True,
                        reason=f'OHLC Breakout Up (confirmed)'
                    )

                elif current_price < target_low:
                    sl = target_high if self.sl_type == "opposite" else ref_mid
                    risk = sl - current_price
                    self._tp_price = current_price - risk * self.rr_ratio
                    self._pending_ref_time = ref_time

                    logger.info(
                        f"OHLC SHORT (confirmed): Price {current_price} < Target {target_low} | "
                        f"SL: {sl} | TP: {self._tp_price:.5f}"
                    )
                    return EntrySignal(
                        side='short',
                        trigger_price=current_price,
                        stop_loss=sl,
                        immediate=True,
                        reason=f'OHLC Breakout Down (confirmed)'
                    )

            else:
                # --- Breakout mode: pending stop-market orders ---
                # Direction based on price position relative to ref_mid
                if current_price >= ref_mid:
                    # Trending toward upper target → long pending
                    trigger = target_high + actual_pip_offset
                    sl = target_low if self.sl_type == "opposite" else ref_mid

                    if current_price >= trigger:
                        # Already above trigger → immediate market
                        risk = current_price - sl
                        self._tp_price = current_price + risk * self.rr_ratio
                        self._pending_ref_time = ref_time

                        logger.info(
                            f"OHLC LONG (immediate breakout): Price {current_price} >= {trigger}"
                        )
                        return EntrySignal(
                            side='long',
                            trigger_price=current_price,
                            stop_loss=sl,
                            immediate=True,
                            reason='OHLC Breakout Up (immediate)'
                        )
                    else:
                        # Place pending stop-market
                        risk = trigger - sl
                        self._tp_price = trigger + risk * self.rr_ratio
                        self._pending_ref_time = ref_time

                        logger.info(
                            f"OHLC LONG (pending): trigger {trigger}, current {current_price}"
                        )
                        return EntrySignal(
                            side='long',
                            trigger_price=trigger,
                            stop_loss=sl,
                            immediate=False,
                            reason='OHLC Breakout Up (pending)'
                        )
                else:
                    # Trending toward lower target → short pending
                    trigger = target_low - actual_pip_offset
                    sl = target_high if self.sl_type == "opposite" else ref_mid

                    if current_price <= trigger:
                        risk = sl - current_price
                        self._tp_price = current_price - risk * self.rr_ratio
                        self._pending_ref_time = ref_time

                        logger.info(
                            f"OHLC SHORT (immediate breakout): Price {current_price} <= {trigger}"
                        )
                        return EntrySignal(
                            side='short',
                            trigger_price=current_price,
                            stop_loss=sl,
                            immediate=True,
                            reason='OHLC Breakout Down (immediate)'
                        )
                    else:
                        risk = sl - trigger
                        self._tp_price = trigger - risk * self.rr_ratio
                        self._pending_ref_time = ref_time

                        logger.info(
                            f"OHLC SHORT (pending): trigger {trigger}, current {current_price}"
                        )
                        return EntrySignal(
                            side='short',
                            trigger_price=trigger,
                            stop_loss=sl,
                            immediate=False,
                            reason='OHLC Breakout Down (pending)'
                        )

            return None

        except Exception as e:
            logger.error(f"Exception generating OHLC entry signal: {e}")
            logger.error(traceback.format_exc())
            return None

    # ======================================================================
    # LIVE — Exit signal
    # ======================================================================

    def generate_exit_signal(
        self,
        setup_id: str,
        position_side: str,
        indicators_data: Dict[str, Any]
    ) -> Optional[ExitSignal]:
        """
        OHLC Breakout Exit:
        - TP check: current price crossed TP level (candle-close based).
        - SL is handled by exchange order (placed by engine at entry).
        - No signal-based exits.
        """
        try:
            current_price = indicators_data.get("current_price", 0)
            tp = indicators_data.get("_tp_price", 0) or self._tp_price

            if tp <= 0 or current_price <= 0:
                return None

            ref = indicators_data.get("ohlc_ref", {})
            ref_mid = ref.get("ref_mid", 0)

            if position_side == "long" and current_price >= tp:
                logger.info(
                    f"OHLC TP HIT (LONG): Price {current_price} >= TP {tp:.5f}"
                )
                return ExitSignal(
                    reason=f"Take Profit ({tp:.5f})",
                    stop_loss=ref_mid
                )
            elif position_side == "short" and current_price <= tp:
                logger.info(
                    f"OHLC TP HIT (SHORT): Price {current_price} <= TP {tp:.5f}"
                )
                return ExitSignal(
                    reason=f"Take Profit ({tp:.5f})",
                    stop_loss=ref_mid
                )

            return None

        except Exception as e:
            logger.error(f"Exception generating OHLC exit signal: {e}")
            return None

    # ======================================================================
    # LIVE — Pending order invalidation
    # ======================================================================

    def should_invalidate_pending_entry(
        self,
        pending_side: str,
        indicators_data: Dict[str, Any]
    ) -> bool:
        """
        Cancel pending OHLC breakout entry if:
        - The reference candle has changed (new levels formed).
        """
        ref = indicators_data.get("ohlc_ref")
        if not ref:
            return False

        current_ref_time = ref.get("ref_time", 0)

        if self._pending_ref_time and current_ref_time != self._pending_ref_time:
            logger.info(
                f"OHLC INVALIDATE pending {pending_side.upper()}: "
                f"reference candle changed ({self._pending_ref_time} → {current_ref_time})"
            )
            return True

        return False

    # ======================================================================
    # LIVE — Cache mapping for dashboard / screener
    # ======================================================================

    def get_cache_mapping(self, indicators_data: Dict[str, Any]) -> Dict[str, Any]:
        """Map OHLC Breakout results to IndicatorCache fields for dashboard display."""
        ref = indicators_data.get("ohlc_ref", {})
        target_high = ref.get("target_high", 0.0)
        target_low = ref.get("target_low", 0.0)
        ref_mid = ref.get("ref_mid", 0.0)
        current_price = indicators_data.get("current_price", 0.0)
        status = indicators_data.get("status", "Waiting")

        # Map status to signal for screener flip detection
        if status == "High Broken":
            signal = 1
        elif status == "Low Broken":
            signal = -1
        else:
            signal = 0

        signal_text = status
        merge_text = "Yes" if self.use_prev_candle else "No"

        return {
            "current_price": current_price,
            "primary_name": f"OHLC({self.reference_time})",
            "primary_signal": signal,
            "primary_signal_text": signal_text,
            "primary_value": target_high,
            "secondary_name": "Target Low",
            "secondary_signal": signal,
            "secondary_signal_text": signal_text,
            "secondary_value": target_low,
            "strategy_state": {
                "primary_signal": signal,
                "ref_time": ref.get("ref_time", 0),
                "tp_price": self._tp_price,
            },
            "display_details": {
                "Status": signal_text,
                "Target High": target_high,
                "Target Low": target_low,
                "Ref Mid (SL)": ref_mid,
                "Entry Mode": self.entry_mode.title(),
                "SL Type": self.sl_type.title(),
                "RR Ratio": f"1:{self.rr_ratio}",
                "Merge Prev": merge_text,
            }
        }
