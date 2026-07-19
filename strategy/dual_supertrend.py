"""
Dual SuperTrend breakout strategy (Perusu entry + Sirusu exit).
Conforms to BaseStrategy interface for modular engine execution.

Entry Logic:
- Perusu (20,20) signal flip triggers breakout entry order
- Entry at LATEST candle HIGH/LOW + 1 pip (stop-market order)
- OR immediate market execution if price already broke

Exit Logic:
- Sirusu (10,10) signal flip triggers market exit
- Sirusu value used as stop-loss (additional protection)
"""

import logging
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime
import numpy as np
import pandas as pd
from indicators.supertrend import SuperTrend, SIGNAL_UPTREND, SIGNAL_DOWNTREND
from indicators.signal_generator import SignalGenerator
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles, get_product_by_symbol
from config.constants import (
    PERUSU_ATR_LENGTH, PERUSU_FACTOR,
    SIRUSU_ATR_LENGTH, SIRUSU_FACTOR,
    BREAKOUT_PIP_OFFSET,
    TIMEFRAME_MAPPING,
    TIMEFRAME_SECONDS,
    CANDLE_CLOSE_BUFFER_SECONDS
)
from utils.timeframe import get_timeframe_seconds
from strategy.base import BaseStrategy, EntrySignal, ExitSignal

logger = logging.getLogger(__name__)


class DualSuperTrendStrategy(BaseStrategy):
    """
    Dual SuperTrend breakout + trailing stop strategy.

    Entry Logic:
    - Perusu (20,20) signal flip triggers breakout entry order
    - Entry at LATEST candle HIGH/LOW + 1 pip (stop-market order)
    - OR immediate market execution if price already broke

    Exit Logic:
    - Sirusu (10,10) signal flip triggers market exit
    - Sirusu value used as stop-loss (additional protection)
    """

    def __init__(self, params: Dict[str, Any] = None):
        """Initialize strategy with dynamic or default indicators."""
        self.params = params or {}

        self.perusu_atr = self.params.get("perusu_atr", PERUSU_ATR_LENGTH)
        self.perusu_factor = self.params.get("perusu_factor", PERUSU_FACTOR)
        self.sirusu_atr = self.params.get("sirusu_atr", SIRUSU_ATR_LENGTH)
        self.sirusu_factor = self.params.get("sirusu_factor", SIRUSU_FACTOR)

        self.perusu = SuperTrend(
            atr_length=int(self.perusu_atr),
            factor=float(self.perusu_factor),
            name="Perusu"
        )

        self.sirusu = SuperTrend(
            atr_length=int(self.sirusu_atr),
            factor=float(self.sirusu_factor),
            name="Sirusu"
        )

        self.signal_generator = SignalGenerator()

        self._last_fetch_time: Dict[str, datetime] = {}
        self._last_candle_count: Dict[str, int] = {}
        self._last_processed_candle_time: Dict[str, int] = {}


    def generate_backtest_signals(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """Vectorized signal generation for the backtester."""
        candles = df.to_dict('records')
        p_series = self.perusu.calculate(candles, return_series=True)
        s_series = self.sirusu.calculate(candles, return_series=True)
        
        n = len(df)
        if not p_series or not s_series:
            return {
                "entry_signal": np.zeros(n, dtype=int),
                "exit_long": np.zeros(n, dtype=bool),
                "exit_short": np.zeros(n, dtype=bool),
                "sl_price_long": np.zeros(n),
                "sl_price_short": np.zeros(n),
                "indicator_value": np.zeros(n)
            }
            
        p_signal = p_series["signal"]
        s_signal = s_series["signal"]
        s_val = s_series["supertrend"]
        
        prev_p_signal = np.roll(p_signal, 1)
        prev_p_signal[0] = p_signal[0]
        
        prev_s_signal = np.roll(s_signal, 1)
        prev_s_signal[0] = s_signal[0]
        
        entry_signal = np.zeros(n, dtype=int)
        
        # Perusu flips
        entry_signal[(prev_p_signal == SIGNAL_DOWNTREND) & (p_signal == SIGNAL_UPTREND)] = 1
        entry_signal[(prev_p_signal == SIGNAL_UPTREND) & (p_signal == SIGNAL_DOWNTREND)] = -1
        
        # Dual ST uses Sirusu flips for exits
        exit_long = (prev_s_signal == SIGNAL_UPTREND) & (s_signal == SIGNAL_DOWNTREND)
        exit_short = (prev_s_signal == SIGNAL_DOWNTREND) & (s_signal == SIGNAL_UPTREND)
        
        return {
            "entry_signal": entry_signal,
            "exit_long": exit_long,
            "exit_short": exit_short,
            "sl_price_long": s_val,
            "sl_price_short": s_val,
            "indicator_value": p_series["supertrend"]
        }

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def _is_candle_closed(self, candles: List[Dict[str, Any]], timeframe: str) -> Dict[str, Any]:
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

        if is_ready:
            logger.info(f"Candle CLOSED and READY (waited {buffer_seconds}s buffer)")

        return {
            'is_closed': is_ready,
            'seconds_until_ready': seconds_until_ready,
            'reason': 'Candle closed and buffered' if is_ready else f'Waiting {seconds_until_ready}s'
        }

    async def calculate_indicators(self, client: DeltaExchangeClient, symbol: str, timeframe: str, skip_boundary_check: bool = False, force_recalc: bool = False, historical_candles: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """Calculate both Perusu and Sirusu indicators with GUARANTEED FRESH DATA."""
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()

            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"Unknown timeframe: {timeframe}")
                return None

            resolution = TIMEFRAME_MAPPING[timeframe]
            timeframe_requirements = {
                "1m": 1000, "2m": 1000, "3m": 1000, "4m": 1000, "5m": 1000, "10m": 1000, "15m": 1000,
                "20m": 1000, "30m": 1000, "45m": 1000, "1h": 1000, "2h": 1000, "3h": 1000, "4h": 1000,
                "6h": 1000, "8h": 1000, "12h": 1000, "1d": 1000, "2d": 1000, "3d": 1000, "7d": 1000, "1w": 1000,
            }
            required_candles = timeframe_requirements.get(timeframe, 1000)
            timeframe_seconds = get_timeframe_seconds(timeframe)

            if historical_candles is not None:
                candles = historical_candles
            else:
    # Efficient Step 1: Only fetch TWO latest candles to check last candle status
                logger.info(f"Checking latest candle close status for {symbol} ({timeframe})")
                latest_candles = await get_candles(client, symbol, timeframe, limit=2)
    
                if not latest_candles:
                    logger.info("Market is quiet. Skipping calculation until new volume arrives.")
                    return None
    
                candle_status = self._is_candle_closed(latest_candles, timeframe)
                if not candle_status["is_closed"]:
                    if not skip_boundary_check:
                        wait_time = candle_status["seconds_until_ready"]
                        logger.debug(
                            f"Candle for {symbol} {timeframe} not fully closed "
                            f"(~{wait_time}s remaining). Skipping calculation."
                        )
                        return None
    
                # Efficient Step 2: Fetch ALL candles
                logger.info(f"FETCHING FRESH candles: {required_candles} candles for {symbol} ({timeframe})")
                end_time = int(datetime.utcnow().timestamp())
                start_time = end_time - int(timeframe_seconds * required_candles * 1.2)
                candles = await get_candles(client, symbol, timeframe, start_time=start_time, end_time=end_time, limit=required_candles)

            logger.info(f"Fetched candles for {symbol} {timeframe}: count={len(candles) if candles else 0}")
            if candles:
                logger.info(f"First candle: {candles[0]}")
                logger.info(f"Last candle:  {candles[-1]}")

            if not candles:
                logger.error(f"No candles available for breakout for {symbol} {timeframe}")
                return None

            # Gather latest candle info and prevent duplicate processing
            actual_count = len(candles)
            latest_candle = candles[-1]
            latest_candle_time = latest_candle.get("time", 0)
            prev_high = float(latest_candle.get("high", 0))
            prev_low = float(latest_candle.get("low", 0))
            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed:
                if not force_recalc:
                    logger.debug(f"Already processed candle {latest_candle_time} for {symbol} {timeframe}, skipping.")
                    return None
                else:
                    logger.info(f"Force recalculating indicators for {symbol} {timeframe} (for SL placement)")

            candle_status = self._is_candle_closed(candles, timeframe)
            if not candle_status["is_closed"]:
                if skip_boundary_check:
                    logger.info(f"Secondary candle check: {symbol} {timeframe} not fully closed; continuing (boundary check skipped).")
                else:
                    logger.debug(f"Candle not fully closed for {symbol} ({candle_status['reason']}). Skipping calculation.")
                    return None

            # Sufficient data?
            min_required = max(self.perusu_atr, self.sirusu_atr) + 10
            if actual_count < min_required:
                logger.debug(f"INSUFFICIENT DATA: got {actual_count}, need at least {min_required}")
                return None
            if actual_count < required_candles:
                logger.warning(f"Got {actual_count} candles, wanted {required_candles}")

            # Calculate Perusu & Sirusu
            logger.info(f"Calculating PERUSU (ATR period={self.perusu_atr}, factor={self.perusu_factor})")
            perusu_result = self.perusu.calculate(candles)
            if not perusu_result:
                logger.error(f"Failed to calculate Perusu for {symbol}")
                return None

            logger.info(f"Calculating SIRUSU (ATR period={self.sirusu_atr}, factor={self.sirusu_factor})")
            sirusu_result = self.sirusu.calculate(candles)
            if not sirusu_result:
                logger.error(f"Failed to calculate Sirusu for {symbol}")
                return None

            # Build result
            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": actual_count,
                "candles_requested": required_candles,
                "candle_status": candle_status,
                "perusu": perusu_result,
                "sirusu": sirusu_result,
                "latest_closed_candle": {"high": prev_high, "low": prev_low},
                "current_price": perusu_result.get('latest_close', 0)
            }

            # Mark as processed
            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            self._last_processed_candle_time[cache_key] = latest_candle_time

            logger.info(f"INDICATORS CALCULATED SUCCESSFULLY (Chart-Accurate)")
            logger.info(f"   Perusu: {perusu_result['signal_text']} @ ${perusu_result['supertrend_value']:.5f}")
            logger.info(f"   Sirusu: {sirusu_result['signal_text']} @ ${sirusu_result['supertrend_value']:.5f}")
            logger.info(f"   Current Price: ${perusu_result.get('latest_close', 0):.5f}")
            logger.info(f"   Latest Candle: High ${prev_high:.5f}, Low ${prev_low:.5f}")
            logger.info(f"   ATR(20): {perusu_result.get('atr', 0):.6f}")

            return result

        except Exception as e:
            logger.error(f"Exception calculating indicators: {e}")
            logger.error(traceback.format_exc())
            return None

    def _detect_signal_flip(self, current_signal: int, last_signal: Optional[int]) -> Optional[str]:
        """Detect if Perusu signal has flipped from last known state."""
        if last_signal is None:
            logger.info(f"Initializing Perusu state: {'Uptrend' if current_signal == 1 else 'Downtrend'} (waiting for flip)")
            return None
        if current_signal == last_signal:
            return None
        if current_signal == 1 and last_signal == -1:
            logger.info(f"Perusu FLIP: Downtrend -> Uptrend (LONG entry signal)")
            return "long"
        elif current_signal == -1 and last_signal == 1:
            logger.info(f"Perusu FLIP: Uptrend -> Downtrend (SHORT entry signal)")
            return "short"
        return None

    def generate_entry_signal(self, setup_id: str, previous_state: Optional[Dict[str, Any]], indicators_data: Dict[str, Any]) -> Optional[EntrySignal]:
        """Generate entry signal based on Perusu flip + breakout logic."""
        try:
            perusu = indicators_data.get("perusu")
            previous_candle = indicators_data.get("latest_closed_candle", {})
            current_price = indicators_data.get("current_price")

            if not perusu or not previous_candle or not current_price:
                logger.error("Missing indicator data for entry signal")
                return None

            prev_high = previous_candle.get("high")
            prev_low = previous_candle.get("low")
            if not prev_high or not prev_low:
                logger.error("Missing latest candle high/low")
                return None

            # Extract last primary signal from previous_state
            last_primary_signal = previous_state.get("primary_signal") if previous_state else None
            # Backwards compat: fallback to old key name
            if last_primary_signal is None and previous_state:
                last_primary_signal = previous_state.get("perusu_signal")
            current_signal = perusu.get("signal")
            entry_side = self._detect_signal_flip(current_signal, last_primary_signal)

            if not entry_side:
                return None

            sirusu_value = indicators_data.get('sirusu', {}).get('supertrend_value', 0)

            if entry_side == "long":
                trigger_price = prev_high + BREAKOUT_PIP_OFFSET

                if current_price >= trigger_price:
                    logger.warning(f"Price already above breakout level! Using MARKET order (immediate)")
                    return EntrySignal(
                        side='long',
                        trigger_price=current_price,
                        stop_loss=sirusu_value,
                        immediate=True,
                        reason='Perusu flip to uptrend (immediate)'
                    )
            else:
                trigger_price = prev_low - BREAKOUT_PIP_OFFSET

                if current_price <= trigger_price:
                    logger.warning(f"Price already below breakout level! Using MARKET order (immediate)")
                    return EntrySignal(
                        side='short',
                        trigger_price=current_price,
                        stop_loss=sirusu_value,
                        immediate=True,
                        reason='Perusu flip to downtrend (immediate)'
                    )

            logger.info(f"Entry signal generated: {entry_side.upper()} breakout trigger: ${trigger_price:.5f}")

            return EntrySignal(
                side=entry_side,
                trigger_price=trigger_price,
                stop_loss=sirusu_value,
                immediate=False,
                reason=f"Perusu flip to {'uptrend' if entry_side == 'long' else 'downtrend'}"
            )

        except Exception as e:
            logger.error(f"Exception generating entry signal: {e}")
            logger.error(traceback.format_exc())
            return None

    def generate_exit_signal(self, setup_id: str, position_side: str, indicators_data: Dict[str, Any]) -> Optional[ExitSignal]:
        """Generate exit signal based on Sirusu flip."""
        try:
            sirusu = indicators_data.get("sirusu")
            if not sirusu:
                logger.error("Missing Sirusu data for exit signal")
                return None

            current_signal = sirusu.get("signal")
            should_exit = False

            if position_side == "long" and current_signal == -1:
                logger.info(f"Sirusu EXIT signal: Uptrend -> Downtrend (Close LONG)")
                should_exit = True
            elif position_side == "short" and current_signal == 1:
                logger.info(f"Sirusu EXIT signal: Downtrend -> Uptrend (Close SHORT)")
                should_exit = True

            if should_exit:
                return ExitSignal(
                    reason=f"Sirusu flip to {'uptrend' if current_signal == 1 else 'downtrend'}",
                    stop_loss=sirusu['supertrend_value']
                )

            return None

        except Exception as e:
            logger.error(f"Exception generating exit signal: {e}")
            logger.error(traceback.format_exc())
            return None

    def should_invalidate_pending_entry(self, pending_side: str, indicators_data: Dict[str, Any]) -> bool:
        """Check if Sirusu flipped against pending entry direction."""
        sirusu = indicators_data.get("sirusu")
        if not sirusu:
            return False
        current_signal = sirusu.get("signal")
        if pending_side == "long" and current_signal == -1:
            return True
        if pending_side == "short" and current_signal == 1:
            return True
        return False

    def get_cache_mapping(self, indicators_data: Dict[str, Any]) -> Dict[str, Any]:
        """Map Dual ST results to IndicatorCache fields."""
        perusu = indicators_data.get("perusu", {})
        sirusu = indicators_data.get("sirusu", {})
        p_signal = perusu.get("signal", 0)
        s_signal = sirusu.get("signal", 0)
        p_val = perusu.get("supertrend_value", 0.0)
        s_val = sirusu.get("supertrend_value", 0.0)
        p_text = perusu.get("signal_text", "Unknown")
        s_text = sirusu.get("signal_text", "Unknown")
        p_emoji = "🔵" if p_signal == 1 else "🔴"
        s_emoji = "🔵" if s_signal == 1 else "🔴"
        return {
            "current_price": indicators_data.get("current_price", 0.0),
            "primary_name": "Perusu",
            "primary_signal": p_signal,
            "primary_signal_text": p_text,
            "primary_value": p_val,
            "secondary_name": "Sirusu",
            "secondary_signal": s_signal,
            "secondary_signal_text": s_text,
            "secondary_value": s_val,
            "strategy_state": {
                "primary_signal": p_signal,
            },
            "display_details": {
                f"Perusu ST": f"{p_emoji} {p_text} @ ${p_val}",
                f"Sirusu ST": f"{s_emoji} {s_text} @ ${s_val}",
                "ATR (Perusu)": perusu.get("atr", 0.0),
                "ATR (Sirusu)": sirusu.get("atr", 0.0),
            }
        }
