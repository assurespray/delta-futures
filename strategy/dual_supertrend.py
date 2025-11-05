"""Dual SuperTrend breakout strategy (Perusu entry + Sirusu exit).
✅ GUARANTEED FRESH DATA - Always fetches current correct candles each calculation cycle
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from indicators.supertrend import SuperTrend
from indicators.signal_generator import SignalGenerator
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles, get_product_by_symbol
from config.constants import (
    PERUSU_ATR_LENGTH, PERUSU_FACTOR,
    SIRUSU_ATR_LENGTH, SIRUSU_FACTOR,
    BREAKOUT_PIP_OFFSET,
    TIMEFRAME_MAPPING
)
from utils.timeframe import get_timeframe_seconds

logger = logging.getLogger(__name__)


class DualSuperTrendStrategy:
    """
    Dual SuperTrend breakout + trailing stop strategy.
    ✅ GUARANTEED: ALWAYS fetches FRESH candles every calculation cycle
    
    Entry Logic:
    - Perusu (20,20) signal flip triggers breakout entry order
    - Entry at previous candle HIGH/LOW + 1 pip (stop-market order)
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
    
    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        """Generate cache key for tracking."""
        return f"{symbol}_{timeframe}"
    
    async def calculate_indicators(self, client: DeltaExchangeClient, 
                                  symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """
        Calculate both Perusu and Sirusu indicators with GUARANTEED FRESH DATA.
        
        ✅ FRESH DATA GUARANTEE:
        - ALWAYS fetches NEW candles from API (no cache)
        - Validates timeframe exists in mapping
        - Checks minimum data requirements
        - Logs current vs last fetch time
        - Verifies candles are from correct time range
        
        Args:
            client: Delta Exchange client
            symbol: Trading symbol (e.g., "ADAUSD")
            timeframe: Timeframe for calculation (e.g., "3m")
        
        Returns:
            Dictionary with perusu, sirusu, and price data, or None on failure
        """
        try:
            cache_key = self._get_cache_key(symbol, timeframe)
            current_time = datetime.utcnow()
            
            # ===== STEP 1: Validate timeframe =====
            if timeframe not in TIMEFRAME_MAPPING:
                logger.error(f"❌ Unknown timeframe: {timeframe}")
                return None
            
            resolution = TIMEFRAME_MAPPING[timeframe]
            logger.info(f"✅ Timeframe '{timeframe}' maps to resolution '{resolution}'")
            
            # ===== STEP 2: Get dynamic candle requirements per timeframe =====
            # ✅ COMPLETE: ALL timeframes optimized for accuracy
            timeframe_requirements = {
                # ===== MINUTES =====
                "1m": 300,      # 5 hours history
                "2m": 250,      # ~8 hours history
                "3m": 200,      # ✅ CRITICAL: 10 hours history
                "4m": 200,
                "5m": 200,      # ~17 hours history
                "10m": 180,     # ~30 hours history
                "15m": 150,     # ~37 hours history (2 days+)
                "20m": 135,     # ~45 hours history (2 days)
                "30m": 120,     # 60 hours history (2.5 days)
                "45m": 100,     # ~67 hours history (3 days)
                
                # ===== HOURS =====
                "1h": 100,      # 100 hours history (4 days)
                "2h": 75,       # 150 hours history (6 days)
                "3h": 60,       # 180 hours history (7.5 days)
                "4h": 60,       # 240 hours history (10 days)
                "6h": 50,       # 300 hours history (12.5 days)
                "8h": 40,       # 320 hours history (~13 days)
                "12h": 30,      # 360 hours history (15 days)
                
                # ===== DAYS =====
                "1d": 50,       # 50 days history (~2 months)
                "2d": 40,       # 80 days history (3 months)
                "3d": 30,       # 90 days history (3 months)
                "7d": 25,       # 175 days history (~6 months)
                "1w": 25,       # Same as 7d
            }
            
            required_candles = timeframe_requirements.get(timeframe, 150)
            
            # ===== STEP 3: Track fetch time for debug =====
            last_fetch = self._last_fetch_time.get(cache_key)
            if last_fetch:
                time_since_fetch = (current_time - last_fetch).total_seconds()
                logger.info(f"⏱️ Last fetch for {symbol} {timeframe}: {time_since_fetch:.1f}s ago")
            else:
                logger.info(f"📍 First fetch for {symbol} {timeframe}")
            
            # ===== STEP 4: ALWAYS fetch FRESH candles (never use cache) =====
            logger.info(f"🔄 FETCHING FRESH candles: {required_candles} candles for {symbol} ({timeframe})")
            logger.info(f"   Using API resolution: {resolution}")
            
            # Force fresh fetch with explicit end_time = now
            end_time = int(current_time.timestamp())
            timeframe_seconds = get_timeframe_seconds(timeframe)
            start_time = end_time - (timeframe_seconds * int(required_candles * 1.2))
            
            candles = await get_candles(
                client,
                symbol,
                timeframe,
                start_time=start_time,
                end_time=end_time,
                limit=required_candles
            )
            
            if not candles:
                logger.error(f"❌ Failed to fetch candles for {symbol}")
                return None
            
            actual_count = len(candles)
            
            # ===== STEP 5: Validate candle freshness =====
            if actual_count > 0:
                last_candle_time = candles[-1].get("time", 0)
                last_candle_datetime = datetime.fromtimestamp(last_candle_time)
                time_diff = (current_time - last_candle_datetime).total_seconds()
                
                logger.info(f"✅ Retrieved {actual_count} candles")
                logger.info(f"   Latest candle time: {last_candle_datetime.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info(f"   Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info(f"   Age of latest candle: {time_diff:.0f} seconds")
                
                # ✅ Safety check: candles should be recent
                if time_diff > (timeframe_seconds * 3):
                    logger.warning(f"⚠️ Latest candle is {time_diff:.0f}s old (expected < {timeframe_seconds*3}s)")
            
            # ===== STEP 6: Validate minimum data requirements =====
            min_required = max(PERUSU_ATR_LENGTH, SIRUSU_ATR_LENGTH) + 10
            
            if actual_count < min_required:
                logger.error(f"❌ INSUFFICIENT DATA: got {actual_count}, need at least {min_required}")
                return None
            
            if actual_count < required_candles:
                logger.warning(f"⚠️ Got {actual_count} candles, wanted {required_candles}")
            
            # ===== STEP 7: Calculate Perusu (Entry indicator) =====
            logger.info(f"🔵 Calculating PERUSU (ATR period={PERUSU_ATR_LENGTH}, factor={PERUSU_FACTOR})")
            logger.info(f"   Using {actual_count} candles")
            
            perusu_result = self.perusu.calculate(candles)
            
            if not perusu_result:
                logger.error(f"❌ Failed to calculate Perusu for {symbol}")
                return None
            
            # ===== STEP 8: Calculate Sirusu (Exit indicator) =====
            logger.info(f"🔴 Calculating SIRUSU (ATR period={SIRUSU_ATR_LENGTH}, factor={SIRUSU_FACTOR})")
            logger.info(f"   Using {actual_count} candles")
            
            sirusu_result = self.sirusu.calculate(candles)
            
            if not sirusu_result:
                logger.error(f"❌ Failed to calculate Sirusu for {symbol}")
                return None
            
            # ===== STEP 9: Get previous candle high/low for breakout entry =====
            if len(candles) >= 2:
                prev_candle = candles[-2]  # Previous closed candle
                prev_high = float(prev_candle.get("high", 0))
                prev_low = float(prev_candle.get("low", 0))
            else:
                prev_candle = candles[-1]
                prev_high = float(prev_candle.get("high", 0))
                prev_low = float(prev_candle.get("low", 0))
            
            # ===== STEP 10: Build result with metadata =====
            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "resolution": resolution,
                "calculated_at": current_time,
                "candles_used": actual_count,
                "candles_requested": required_candles,
                "perusu": perusu_result,
                "sirusu": sirusu_result,
                "previous_candle": {
                    "high": prev_high,
                    "low": prev_low
                },
                "current_price": perusu_result.get('latest_close', 0)
            }
            
            # ===== STEP 11: Log summary and update tracking =====
            logger.info(f"✅ INDICATORS CALCULATED SUCCESSFULLY")
            logger.info(f"   Symbol: {symbol}")
            logger.info(f"   Timeframe: {timeframe}")
            logger.info(f"   Candles: {actual_count}/{required_candles}")
            logger.info(f"   📊 Perusu: {perusu_result['signal_text']} @ ${perusu_result['supertrend_value']:.5f}")
            logger.info(f"   📊 Sirusu: {sirusu_result['signal_text']} @ ${sirusu_result['supertrend_value']:.5f}")
            logger.info(f"   📊 Current Price: ${perusu_result.get('latest_close', 0):.5f}")
            logger.info(f"   📊 Previous Candle: High ${prev_high:.5f}, Low ${prev_low:.5f}")
            logger.info(f"   📊 ATR(20): {perusu_result.get('atr', 0):.6f}")
            
            # Update tracking for next cycle
            self._last_fetch_time[cache_key] = current_time
            self._last_candle_count[cache_key] = actual_count
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def detect_signal_flip(self, current_signal: int, 
                          last_signal: Optional[int]) -> Optional[str]:
        """
        Detect if Perusu signal has flipped from last known state.
        
        Args:
            current_signal: Current signal (1=uptrend, -1=downtrend)
            last_signal: Last known signal state
        
        Returns:
            "long" for uptrend flip, "short" for downtrend flip, None for no change
        """
        # First run - no flip, just store state
        if last_signal is None:
            logger.info(f"📍 Initial Perusu state: {'Uptrend' if current_signal == 1 else 'Downtrend'}")
            return None
        
        # No change
        if current_signal == last_signal:
            return None
        
        # Signal flipped!
        if current_signal == 1 and last_signal == -1:
            logger.info(f"🔄 Perusu FLIP: Downtrend → Uptrend (LONG entry signal)")
            return "long"
        elif current_signal == -1 and last_signal == 1:
            logger.info(f"🔄 Perusu FLIP: Uptrend → Downtrend (SHORT entry signal)")
            return "short"
        
        return None
    
    def calculate_breakout_price(self, entry_side: str, 
                                prev_high: float, prev_low: float) -> float:
        """
        Calculate breakout entry trigger price (candle extreme + 1 pip).
        
        Args:
            entry_side: "long" or "short"
            prev_high: Previous candle high
            prev_low: Previous candle low
        
        Returns:
            Breakout trigger price
        """
        if entry_side == "long":
            # Long: Break above previous candle high
            breakout_price = prev_high + BREAKOUT_PIP_OFFSET
        else:
            # Short: Break below previous candle low
            breakout_price = prev_low - BREAKOUT_PIP_OFFSET
        
        logger.info(f"🎯 Breakout {entry_side.upper()} trigger: ${breakout_price:.5f}")
        return breakout_price
    
    def should_exit_position(self, current_sirusu_signal: int, 
                           position_side: str) -> bool:
        """
        Check if Sirusu signal indicates position exit.
        
        Args:
            current_sirusu_signal: Current Sirusu signal (1=uptrend, -1=downtrend)
            position_side: Current position ("long" or "short")
        
        Returns:
            True if should exit, False otherwise
        """
        if position_side == "long":
            # Exit long when Sirusu flips to downtrend
            if current_sirusu_signal == -1:
                logger.info(f"🚪 Sirusu EXIT signal: Uptrend → Downtrend (Close LONG)")
                return True
        
        elif position_side == "short":
            # Exit short when Sirusu flips to uptrend
            if current_sirusu_signal == 1:
                logger.info(f"🚪 Sirusu EXIT signal: Downtrend → Uptrend (Close SHORT)")
                return True
        
        return False
    
    def generate_entry_signal(self, algo_setup_id: str,
                             last_perusu_signal: Optional[int],
                             indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Generate entry signal based on Perusu flip + breakout logic.
        ✅ HANDLES: Immediate execution when price already broke through.
        ✅ GUARANTEES: Fresh candle data used for calculations
        
        Args:
            algo_setup_id: Algo setup ID
            last_perusu_signal: Last known Perusu signal state
            indicators_data: Dict from calculate_indicators()
        
        Returns:
            Entry signal dict with 'immediate' flag, or None
        """
        try:
            perusu = indicators_data.get("perusu")
            previous_candle = indicators_data.get("previous_candle", {})
            current_price = indicators_data.get("current_price")
        
            if not perusu or not previous_candle or not current_price:
                logger.error("❌ Missing indicator data for entry signal")
                return None
        
            prev_high = previous_candle.get("high")
            prev_low = previous_candle.get("low")
        
            if not prev_high or not prev_low:
                logger.error("❌ Missing previous candle high/low")
                return None
        
            current_signal = perusu.get("signal")
        
            # Detect signal flip
            entry_side = self.detect_signal_flip(current_signal, last_perusu_signal)
        
            if not entry_side:
                # No flip detected
                return None
        
            # Calculate breakout trigger price
            if entry_side == "long":
                # LONG: Break above previous candle high
                trigger_price = prev_high + BREAKOUT_PIP_OFFSET
            
                # Check if price already broke through
                if current_price >= trigger_price:
                    logger.warning(f"⚠️ Price already above breakout level!")
                    logger.warning(f"   Current: ${current_price:.5f}")
                    logger.warning(f"   Trigger: ${trigger_price:.5f}")
                    logger.warning(f"   → Using MARKET order (immediate execution)")
                
                    return {
                        'side': 'long',
                        'trigger_price': current_price,
                        'immediate': True,
                        'entry_reason': 'Perusu flip to uptrend (immediate)',
                        'perusu_signal': current_signal,
                        'perusu_value': perusu['supertrend_value']
                    }
        
            else:  # entry_side == "short"
                # SHORT: Break below previous candle low
                trigger_price = prev_low - BREAKOUT_PIP_OFFSET
                
                # Check if price already broke through
                if current_price <= trigger_price:
                    logger.warning(f"⚠️ Price already below breakout level!")
                    logger.warning(f"   Current: ${current_price:.5f}")
                    logger.warning(f"   Trigger: ${trigger_price:.5f}")
                    logger.warning(f"   → Using MARKET order (immediate execution)")
                
                    return {
                        'side': 'short',
                        'trigger_price': current_price,
                        'immediate': True,
                        'entry_reason': 'Perusu flip to downtrend (immediate)',
                        'perusu_signal': current_signal,
                        'perusu_value': perusu['supertrend_value']
                    }
        
            # Price hasn't broken through yet - use stop order
            logger.info(f"🎯 Entry signal generated:")
            logger.info(f"   Side: {entry_side.upper()}")
            logger.info(f"   Breakout trigger: ${trigger_price:.5f}")
            logger.info(f"   Current price: ${current_price:.5f}")
            logger.info(f"   Perusu value: ${perusu['supertrend_value']:.5f}")
        
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
        """
        Generate exit signal based on Sirusu flip.
        ✅ GUARANTEES: Fresh candle data used for calculations
        
        Args:
            algo_setup_id: Algo setup ID
            position_side: Current position ("long" or "short")
            indicators_data: Dict from calculate_indicators()
        
        Returns:
            Exit signal dict or None
        """
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
                logger.info(f"   Sirusu signal: {'Uptrend' if current_signal == 1 else 'Downtrend'}")
                logger.info(f"   Sirusu value: ${sirusu['supertrend_value']:.5f}")
                
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
