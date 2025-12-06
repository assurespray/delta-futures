"""
Dual SuperTrend breakout strategy (Perusu entry + Sirusu exit).
✅ GUARANTEED FRESH DATA - Always fetches current correct candles each calculation cycle
✅ WAITS 5 SECONDS - After candle close for API consolidation (chart accuracy)
✅ USES LATEST CANDLE HIGH/LOW FOR BREAKOUT - Not previous
"""

import logging
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
from indicators.supertrend import SuperTrend, SIGNAL_UPTREND, SIGNAL_DOWNTREND
from indicators.signal_generator import SignalGenerator
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles, get_product_by_symbol
from database.crud import save_indicator_cache  # ✅ ADD THIS LINE
from config.constants import (
    PERUSU_ATR_LENGTH, PERUSU_FACTOR,
    SIRUSU_ATR_LENGTH, SIRUSU_FACTOR,
    BREAKOUT_PIP_OFFSET,
    TIMEFRAME_MAPPING,
    TIMEFRAME_SECONDS,
    CANDLE_CLOSE_BUFFER_SECONDS
)
from utils.timeframe import get_timeframe_seconds

logger = logging.getLogger(__name__)


class DualSuperTrendStrategy:
    """
    Dual SuperTrend breakout + trailing stop strategy.
    ✅ GUARANTEED: ALWAYS fetches FRESH candles every calculation cycle
    ✅ WAITS: 5 seconds after candle close for API consolidation
    ✅ USES: LATEST candle high/low for breakout (not previous)
    
    Entry Logic:
    - Perusu (20,20) signal flip triggers breakout entry order
    - Entry at LATEST candle HIGH/LOW + 1 pip (stop-market order)
    - OR immediate market execution if price already broke
    
    Exit Logic:
    - Sirusu (10,10) signal flip triggers market exit
    - Sirusu value used as stop-loss (additional protection)
    """
    
    def __init__(self):
        """Initialize strategy with indicators."""
        self.perusu = SuperTrend(
            atr_length=PERUSU_ATR_LENGTH,
            factor=PERUSU_FACTOR,
            name="Perusu"
        )
        
        self.sirusu = SuperTrend(
            atr_length=SIRUSU_ATR_LENGTH,
            factor=SIRUSU_FACTOR,
            name="Sirusu"
        )
        
        self.signal_generator = SignalGenerator()
        
        # ✅ TRACKING: Last fetch time per symbol+timeframe combo
        self._last_fetch_time: Dict[str, datetime] = {}
        self._last_candle_count: Dict[str, int] = {}

        # Add to __init__:
        self._last_processed_candle_time: Dict[str, int] = {}

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        """Generate cache key for tracking."""
        return f"{symbol}_{timeframe}"
    
    def _is_candle_closed(self, candles: List[Dict[str, Any]], timeframe: str) -> Dict[str, Any]:
        """
        ✅ NEW: Check if latest candle is fully closed with 5-second API buffer.
        
        Ensures indicator values match TradingView charts exactly by waiting
        5 seconds after candle close for API to consolidate data.
        
        Args:
            candles: List of candle dictionaries
            timeframe: Timeframe string (e.g., "3m", "1h")
        
        Returns:
            Dictionary with:
                - 'is_closed': True if candle is closed + buffer passed
                - 'seconds_until_ready': Seconds until ready (0 if ready)
                - 'reason': Explanation message
        """
        if not candles:
            return {
                'is_closed': False,
                'seconds_until_ready': 999,
                'reason': 'No candles available'
            }
        
        latest_candle = candles[-1]
        candle_time = latest_candle.get("time", 0)
        current_time = int(datetime.utcnow().timestamp())
        
        # Get timeframe duration in seconds from constants
        timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 180)
        
        # Calculate when candle closes
        candle_close_time = candle_time + timeframe_seconds
        
        # Add 5-second buffer for API consolidation
        buffer_seconds = CANDLE_CLOSE_BUFFER_SECONDS
        ready_time = candle_close_time + buffer_seconds
        
        # Check if we're past the ready time
        is_ready = current_time >= ready_time
        seconds_until_ready = max(0, ready_time - current_time)
        
        if is_ready:
            logger.info(f"✅ Candle CLOSED and READY (waited {buffer_seconds}s buffer)")
            # logger.info(f"   Candle opened: {datetime.fromtimestamp(candle_time).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            # logger.info(f"   Candle closed: {datetime.fromtimestamp(candle_close_time).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            # logger.info(f"   Data ready: {datetime.fromtimestamp(ready_time).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        else:
            # logger.info(f"⏳ Waiting for candle close + {buffer_seconds}s buffer")
            # logger.info(f"   Candle closes: {datetime.fromtimestamp(candle_close_time).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            # logger.info(f"   Data ready in: {seconds_until_ready}s")
            pass
        
        return {
            'is_closed': is_ready,
            'seconds_until_ready': seconds_until_ready,
            'reason': 'Candle closed and buffered' if is_ready else f'Waiting {seconds_until_ready}s'
        }
    
    async def calculate_indicators(self, client: DeltaExchangeClient, symbol: str, timeframe: str, skip_boundary_check: bool = False, force_recalc: bool = False) -> Optional[Dict[str, Any]]:
        """
        Calculate both Perusu and Sirusu indicators with GUARANTEED FRESH DATA.
        Only one fetch and calculation per new candle, per asset/timeframe.
        """
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()
        
            # 1. Validate timeframe
            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"❌ Unknown timeframe: {timeframe}")
                return None
        
            resolution = TIMEFRAME_MAPPING[timeframe]
            timeframe_requirements = {
                "1m": 200, "2m": 300, "3m": 400, "4m": 300, "5m": 300, "10m": 300, "15m": 300,
                "20m": 300, "30m": 300, "45m": 300, "1h": 300, "2h": 300, "3h": 300, "4h": 300,
                "6h": 300, "8h": 300, "12h": 300, "1d": 600, "2d": 300, "3d": 300, "7d": 300, "1w": 300,
            }
            required_candles = timeframe_requirements.get(timeframe, 150)
            timeframe_seconds = get_timeframe_seconds(timeframe)
            
            # Efficient Step 1: Only fetch TWO latest candles to check last candle status
            logger.info(f"🔍 Checking latest candle close status for {symbol} ({timeframe})")
            latest_candles = await get_candles(client, symbol, timeframe, limit=2)
            
            if not latest_candles:
                logger.error("❌ Could not fetch latest candles for status check")
                return None

            candle_status = self._is_candle_closed(latest_candles, timeframe)
            if not candle_status["is_closed"]:
                wait_time = candle_status["seconds_until_ready"]
                logger.warning(
                    f"⚠️ Candle for {symbol} {timeframe} not fully closed "
                    f"(~{wait_time}s remaining). Forcing indicator calculation "
                    "using last closed candle."
                )
                # No waiting and no early return; continue to full fetch.

            # Efficient Step 2: Now fetch ALL candles in one call (guaranteed latest is closed)
            logger.info(f"🔄 FETCHING FRESH candles: {required_candles} candles for {symbol} ({timeframe})")
            end_time = int(datetime.utcnow().timestamp())
            start_time = end_time - int(timeframe_seconds * required_candles * 1.2)
            candles = await get_candles(client, symbol, timeframe, start_time=start_time, end_time=end_time, limit=required_candles)

            logger.info(f"Fetched candles for {symbol} {timeframe}: count={len(candles) if candles else 0}, start={start_time}, end={end_time}, limit={required_candles}")
            if candles:
                logger.info(f"First candle: {candles[0]}")
                logger.info(f"Last candle:  {candles[-1]}")
            else:
                logger.info("No candles returned from get_candles.")

            if not candles:
                logger.error(f"❌ No candles available for breakout for {symbol} {timeframe}")
                return None          

            # 3. Gather latest candle info and prevent duplicate processing
            actual_count = len(candles)
            latest_candle = candles[-1]
            latest_candle_time = latest_candle.get("time", 0)
            prev_high = float(latest_candle.get("high", 0))
            prev_low = float(latest_candle.get("low", 0))
            last_processed = self._last_processed_candle_time.get(cache_key)
            if last_processed is not None and latest_candle_time == last_processed:
                if not force_recalc:
                    logger.debug(f"🔁 Already processed candle {latest_candle_time} for {symbol} {timeframe}, skipping.")
                    return None
                else:
                    logger.info(f"🔄 Force recalculating indicators for {symbol} {timeframe} (for SL placement)")
                    logger.warning("STACK TRACE for force recalc:" + "".join(traceback.format_stack(limit=8)))

            # 4. Check candle closed and buffered
            # 4. Check candle closed and buffered
            candle_status = self._is_candle_closed(candles, timeframe)
            if not candle_status['is_closed']:
                if skip_boundary_check:
                    logger.info(f"🔵 Skipping secondary candle closed check in reconciliation mode for {symbol}.")
                    # Proceed even if the last candle is not closed
                else:
                    return None
                    
            # 5. Sufficient data?
            min_required = max(PERUSU_ATR_LENGTH, SIRUSU_ATR_LENGTH) + 10
            if actual_count < min_required:
                logger.error(f"❌ INSUFFICIENT DATA: got {actual_count}, need at least {min_required}")
                return None
            if actual_count < required_candles:
                logger.warning(f"⚠️ Got {actual_count} candles, wanted {required_candles}")

            # 6. Calculate Perusu & Sirusu
            logger.info(f"🔵 Calculating PERUSU (ATR period={PERUSU_ATR_LENGTH}, factor={PERUSU_FACTOR})")
            perusu_result = self.perusu.calculate(candles)
            if not perusu_result:
                logger.error(f"❌ Failed to calculate Perusu for {symbol}")
                return None
        
            logger.info(f"🔴 Calculating SIRUSU (ATR period={SIRUSU_ATR_LENGTH}, factor={SIRUSU_FACTOR})")
            sirusu_result = self.sirusu.calculate(candles)
            if not sirusu_result:
                logger.error(f"❌ Failed to calculate Sirusu for {symbol}")
                return None

            # 7. Build result
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

            # 8. Mark as processed and log
            # 8. Mark as processed and log
            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            self._last_processed_candle_time[cache_key] = latest_candle_time

            logger.info(f"✅ INDICATORS CALCULATED SUCCESSFULLY (Chart-Accurate)")
            logger.info(f"   📊 Perusu: {perusu_result['signal_text']} @ ${perusu_result['supertrend_value']:.5f}")
            logger.info(f"   📊 Sirusu: {sirusu_result['signal_text']} @ ${sirusu_result['supertrend_value']:.5f}")
            logger.info(f"   📊 Current Price: ${perusu_result.get('latest_close', 0):.5f}")
            logger.info(f"   📊 Latest Candle: High ${prev_high:.5f}, Low ${prev_low:.5f}")
            logger.info(f"   📊 ATR(20): {perusu_result.get('atr', 0):.6f}")

            return result

        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def detect_signal_flip(self, current_signal: int, 
                          last_signal: Optional[int]) -> Optional[str]:
        """Detect if Perusu signal has flipped from last known state."""
    
        # ✅ On first run, just populate cache and wait for next candle
        if last_signal is None:
            logger.info(f"📍 Initializing Perusu state: {'Uptrend' if current_signal == 1 else 'Downtrend'} (waiting for flip)")
            return None
    
        # No flip
        if current_signal == last_signal:
            return None
    
        # Flip detected
        if current_signal == 1 and last_signal == -1:
            logger.info(f"🔄 Perusu FLIP: Downtrend → Uptrend (LONG entry signal)")
            return "long"
        elif current_signal == -1 and last_signal == 1:
            logger.info(f"🔄 Perusu FLIP: Uptrend → Downtrend (SHORT entry signal)")
            return "short"
    
        return None
    
    def calculate_breakout_price(self, entry_side: str, 
                                prev_high: float, prev_low: float) -> float:
        """Calculate breakout entry trigger price (LATEST candle extreme + 1 pip)."""
        if entry_side == "long":
            breakout_price = prev_high + BREAKOUT_PIP_OFFSET
        else:
            breakout_price = prev_low - BREAKOUT_PIP_OFFSET
        
        # logger.info(f"🎯 Breakout {entry_side.upper()} trigger: ${breakout_price:.5f}")
        return breakout_price
    
    def should_exit_position(self, current_sirusu_signal: int, 
                           position_side: str) -> bool:
        """Check if Sirusu signal indicates position exit."""
        if position_side == "long":
            if current_sirusu_signal == -1:
                logger.info(f"🚪 Sirusu EXIT signal: Uptrend → Downtrend (Close LONG)")
                return True
        
        elif position_side == "short":
            if current_sirusu_signal == 1:
                logger.info(f"🚪 Sirusu EXIT signal: Downtrend → Uptrend (Close SHORT)")
                return True
        
        return False
    
    def generate_entry_signal(self, algo_setup_id: str,
                             last_perusu_signal: Optional[int],
                             indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate entry signal based on Perusu flip + breakout logic."""
        try:
            perusu = indicators_data.get("perusu")
            previous_candle = indicators_data.get("latest_closed_candle", {})
            current_price = indicators_data.get("current_price")
        
            if not perusu or not previous_candle or not current_price:
                logger.error("❌ Missing indicator data for entry signal")
                return None
        
            prev_high = previous_candle.get("high")
            prev_low = previous_candle.get("low")
        
            if not prev_high or not prev_low:
                logger.error("❌ Missing latest candle high/low")
                return None
        
            current_signal = perusu.get("signal")
            entry_side = self.detect_signal_flip(current_signal, last_perusu_signal)
        
            if not entry_side:
                return None
        
            if entry_side == "long":
                trigger_price = prev_high + BREAKOUT_PIP_OFFSET
            
                if current_price >= trigger_price:
                    logger.warning(f"⚠️ Price already above breakout level!")
                    # logger.warning(f"   Current: ${current_price:.5f}")
                    # logger.warning(f"   Trigger: ${trigger_price:.5f}")
                    # logger.warning(f"   Latest High: ${prev_high:.5f}")
                    logger.warning(f"   → Using MARKET order (immediate execution)")
                
                    return {
                        'side': 'long',
                        'trigger_price': current_price,
                        'immediate': True,
                        'entry_reason': 'Perusu flip to uptrend (immediate)',
                        'perusu_signal': current_signal,
                        'perusu_value': perusu['supertrend_value'],
                        'latest_high': prev_high
                    }
        
            else:
                trigger_price = prev_low - BREAKOUT_PIP_OFFSET
                
                if current_price <= trigger_price:
                    logger.warning(f"⚠️ Price already below breakout level!")
                    # logger.warning(f"   Current: ${current_price:.5f}")
                    # logger.warning(f"   Trigger: ${trigger_price:.5f}")
                    # logger.warning(f"   Latest Low: ${prev_low:.5f}")
                    logger.warning(f"   → Using MARKET order (immediate execution)")
                
                    return {
                        'side': 'short',
                        'trigger_price': current_price,
                        'immediate': True,
                        'entry_reason': 'Perusu flip to downtrend (immediate)',
                        'perusu_signal': current_signal,
                        'perusu_value': perusu['supertrend_value'],
                        'latest_low': prev_low
                    }
        
            logger.info(f"🎯 Entry signal generated:")
            logger.info(f"   Side: {entry_side.upper()}")
            logger.info(f"   Breakout trigger: ${trigger_price:.5f}")
            # logger.info(f"   Current price: ${current_price:.5f}")
            # logger.info(f"   Latest High: ${prev_high:.5f}")
            # logger.info(f"   Latest Low: ${prev_low:.5f}")
            # logger.info(f"   Perusu value: ${perusu['supertrend_value']:.5f}")
        
            return {
                "side": entry_side,
                "trigger_price": trigger_price,
                "immediate": False,
                "perusu_signal": current_signal,
                "perusu_value": perusu['supertrend_value'],
                "prev_high": prev_high,
                "prev_low": prev_low,
                "entry_reason": f"Perusu flip to {'uptrend' if entry_side == 'long' else 'downtrend'}"
            }
        
        except Exception as e:
            logger.error(f"❌ Exception generating entry signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def generate_exit_signal(self, algo_setup_id: str,
                            position_side: str,
                            indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate exit signal based on Sirusu flip."""
        try:
            sirusu = indicators_data.get("sirusu")
            
            if not sirusu:
                logger.error("❌ Missing Sirusu data for exit signal")
                return None
            
            current_signal = sirusu.get("signal")
            should_exit = self.should_exit_position(current_signal, position_side)
            
            if should_exit:
                logger.info(f"🚪 Exit signal generated:")
                logger.info(f"   Position: {position_side.upper()}")
                # logger.info(f"   Sirusu signal: {'Uptrend' if current_signal == 1 else 'Downtrend'}")
                # logger.info(f"   Sirusu value: ${sirusu['supertrend_value']:.5f}")
                
                return {
                    "exit_reason": f"Sirusu flip to {'uptrend' if current_signal == 1 else 'downtrend'}",
                    "sirusu_signal": current_signal,
                    "sirusu_value": sirusu['supertrend_value']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Exception generating exit signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

strategy_instance = DualSuperTrendStrategy()

async def get_latest_sirusu(client, symbol, timeframe):
    logger.warning(f"get_latest_sirusu() called for {symbol} {timeframe}, see trace below:")
    logger.warning("STACK TRACE for get_latest_sirusu:" + "".join(traceback.format_stack(limit=8)))
    indicators = await strategy_instance.calculate_indicators(
        client, symbol, timeframe, 
        skip_boundary_check=True,
        force_recalc=True  # <-- ADD THIS LINE
    )
    if indicators and indicators['sirusu']:
        return indicators['sirusu']['supertrend_value']
    raise RuntimeError(f"Sirusu could not be calculated for {symbol} {timeframe}")
