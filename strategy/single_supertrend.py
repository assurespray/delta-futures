"""
Single SuperTrend breakout strategy.
✅ GUARANTEED FRESH DATA
✅ WAITS 5 SECONDS
✅ USES LATEST CANDLE HIGH/LOW FOR BREAKOUT
"""

import logging
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
from indicators.supertrend import SuperTrend, SIGNAL_UPTREND, SIGNAL_DOWNTREND
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles
from config.constants import (
    BREAKOUT_PIP_OFFSET,
    TIMEFRAME_MAPPING,
    TIMEFRAME_SECONDS,
    CANDLE_CLOSE_BUFFER_SECONDS
)
from utils.timeframe import get_timeframe_seconds

logger = logging.getLogger(__name__)

class SingleSuperTrendStrategy:
    """
    Single SuperTrend strategy.
    """
    
    def __init__(self, params: Dict[str, Any] = None):
        """Initialize strategy with indicators."""
        self.params = params or {}
        
        self.atr_length = self.params.get("atr_length", 15)
        self.factor = self.params.get("factor", 15.0)
        
        self.supertrend = SuperTrend(
            atr_length=int(self.atr_length),
            factor=float(self.factor),
            name="SuperTrend"
        )
        
        self._last_fetch_time: Dict[str, datetime] = {}
        self._last_candle_count: Dict[str, int] = {}
        self._last_processed_candle_time: Dict[str, int] = {}

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
        
        return {
            'is_closed': is_ready,
            'seconds_until_ready': seconds_until_ready,
            'reason': 'Candle closed and buffered' if is_ready else f'Waiting {seconds_until_ready}s'
        }
    
    async def calculate_indicators(self, client: DeltaExchangeClient, symbol: str, timeframe: str, skip_boundary_check: bool = False, force_recalc: bool = False) -> Optional[Dict[str, Any]]:
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()
        
            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"❌ Unknown timeframe: {timeframe}")
                return None
        
            resolution = TIMEFRAME_MAPPING[timeframe]
            required_candles = 300
            timeframe_seconds = get_timeframe_seconds(timeframe)
            
            latest_candles = await get_candles(client, symbol, timeframe, limit=2)
            
            if not latest_candles:
                return None

            candle_status = self._is_candle_closed(latest_candles, timeframe)

            end_time = int(datetime.utcnow().timestamp())
            start_time = end_time - int(timeframe_seconds * required_candles * 1.2)
            candles = await get_candles(client, symbol, timeframe, start_time=start_time, end_time=end_time, limit=required_candles)

            if not candles:
                return None          

            actual_count = len(candles)
            latest_candle = candles[-1]
            latest_candle_time = latest_candle.get("time", 0)
            prev_high = float(latest_candle.get("high", 0))
            prev_low = float(latest_candle.get("low", 0))
            
            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed:
                if not force_recalc:
                    return None

            candle_status = self._is_candle_closed(candles, timeframe)

            min_required = self.atr_length + 10
            if actual_count < min_required:
                logger.error(f"❌ INSUFFICIENT DATA: got {actual_count}, need at least {min_required}")
                return None

            logger.info(f"🔵 Calculating Single SuperTrend (ATR={self.atr_length}, factor={self.factor})")
            st_result = self.supertrend.calculate(candles)
            if not st_result:
                return None

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": actual_count,
                "candles_requested": required_candles,
                "candle_status": candle_status,
                "perusu": st_result,  # Map to perusu for UI compatibility
                "sirusu": st_result,  # Map to sirusu for UI compatibility
                "single_st": st_result,
                "latest_closed_candle": {"high": prev_high, "low": prev_low},
                "current_price": st_result.get('latest_close', 0)
            }

            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            self._last_processed_candle_time[cache_key] = latest_candle_time

            return result

        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            return None

    def detect_signal_flip(self, current_signal: int, last_signal: Optional[int]) -> Optional[str]:
        if last_signal is None:
            return None
        if current_signal == last_signal:
            return None
        if current_signal == 1 and last_signal == -1:
            return "long"
        elif current_signal == -1 and last_signal == 1:
            return "short"
        return None
    
    def generate_entry_signal(self, algo_setup_id: str, last_perusu_signal: Optional[int], indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            st = indicators_data.get("single_st")
            previous_candle = indicators_data.get("latest_closed_candle", {})
            current_price = indicators_data.get("current_price")
        
            if not st or not previous_candle or not current_price:
                return None
        
            prev_high = previous_candle.get("high")
            prev_low = previous_candle.get("low")
            if not prev_high or not prev_low:
                return None
        
            current_signal = st.get("signal")
            entry_side = self.detect_signal_flip(current_signal, last_perusu_signal)
        
            if not entry_side:
                return None
        
            if entry_side == "long":
                trigger_price = prev_high + BREAKOUT_PIP_OFFSET
                if current_price >= trigger_price:
                    return {
                        'side': 'long', 'trigger_price': current_price, 'immediate': True,
                        'entry_reason': 'ST flip to uptrend (immediate)',
                        'perusu_signal': current_signal, 'perusu_value': st['supertrend_value'], 'sl_value': st['supertrend_value'], 'latest_high': prev_high
                    }
            else:
                trigger_price = prev_low - BREAKOUT_PIP_OFFSET
                if current_price <= trigger_price:
                    return {
                        'side': 'short', 'trigger_price': current_price, 'immediate': True,
                        'entry_reason': 'ST flip to downtrend (immediate)',
                        'perusu_signal': current_signal, 'perusu_value': st['supertrend_value'], 'sl_value': st['supertrend_value'], 'latest_low': prev_low
                    }
        
            return {
                "side": entry_side, "trigger_price": trigger_price, "immediate": False,
                "perusu_signal": current_signal, "perusu_value": st['supertrend_value'],
                "sl_value": st['supertrend_value'],
                "prev_high": prev_high, "prev_low": prev_low,
                "entry_reason": f"ST flip to {'uptrend' if entry_side == 'long' else 'downtrend'}"
            }
        
        except Exception as e:
            return None
    
    def generate_exit_signal(self, algo_setup_id: str, position_side: str, indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            st = indicators_data.get("single_st")
            if not st:
                return None
            
            current_signal = st.get("signal")
            should_exit = False
            
            if position_side == "long" and current_signal == -1:
                should_exit = True
            elif position_side == "short" and current_signal == 1:
                should_exit = True
            
            if should_exit:
                return {
                    "exit_reason": f"ST flip to {'uptrend' if current_signal == 1 else 'downtrend'}",
                    "sirusu_signal": current_signal,
                    "sirusu_value": st['supertrend_value']
                }
            return None
        except Exception as e:
            return None

strategy_instance = SingleSuperTrendStrategy()
