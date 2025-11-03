"""Dual SuperTrend breakout strategy (Perusu entry + Sirusu exit)."""
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
    BREAKOUT_PIP_OFFSET
)

logger = logging.getLogger(__name__)


class DualSuperTrendStrategy:
    """
    ✅ FIXED: Dual SuperTrend breakout strategy.
    
    Entry Logic:
    ✅ ONLY on Perusu(20,20) FLIP (NOT Sirusu!)
    - Perusu signal must flip (uptrend or downtrend)
    - Entry at current price (market order)
    
    Exit Logic:
    - Sirusu(10,10) signal flip triggers market exit
    - Sirusu value used ONLY for stop-loss level
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
    
    async def calculate_indicators(self, client: DeltaExchangeClient, 
                                  symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """
        Calculate both Perusu and Sirusu indicators.
        
        Args:
            client: Delta Exchange client
            symbol: Trading symbol (e.g., "ADAUSD")
            timeframe: Timeframe string (e.g., "1m", "15m")
        
        Returns:
            Dictionary with perusu and sirusu data, or None on failure
        """
        try:
            # Get candles
            candles = await get_candles(client, symbol, timeframe)
            
            if not candles or len(candles) < max(PERUSU_ATR_LENGTH, SIRUSU_ATR_LENGTH) + 10:
                logger.error(f"❌ Insufficient candle data for {symbol} {timeframe}")
                return None
            
            # Calculate Perusu
            perusu_result = self.perusu.calculate(candles)
            
            if not perusu_result:
                logger.error(f"❌ Perusu calculation failed for {symbol}")
                return None
            
            # Calculate Sirusu
            sirusu_result = self.sirusu.calculate(candles)
            
            if not sirusu_result:
                logger.error(f"❌ Sirusu calculation failed for {symbol}")
                return None
            
            # Get previous candle high/low
            if len(candles) >= 2:
                prev_candle = candles[-2]  # Previous closed candle
                prev_high = float(prev_candle.get("high", 0))
                prev_low = float(prev_candle.get("low", 0))
            else:
                prev_high = float(candles[-1].get("high", 0))
                prev_low = float(candles[-1].get("low", 0))
            
            logger.info(f"📊 {symbol} {timeframe} - Indicators calculated")
            logger.info(f"   Perusu(20,20): {perusu_result['signal_text']}, Value: ${perusu_result['supertrend_value']:.5f}")
            logger.info(f"   Sirusu(10,10): {sirusu_result['signal_text']}, Value: ${sirusu_result['supertrend_value']:.5f}")
            logger.info(f"   Current Price: ${perusu_result['latest_close']:.5f}")
            
            return {
                "perusu": perusu_result,
                "sirusu": sirusu_result,
                "previous_candle": {
                    "high": prev_high,
                    "low": prev_low
                },
                "current_price": perusu_result['latest_close']
            }
            
        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def detect_signal_flip(self, current_signal: int, 
                          last_signal: Optional[int]) -> Optional[str]:
        """
        ✅ FIXED: Detect ONLY Perusu signal flip.
        
        Args:
            current_signal: Current signal (1=uptrend, -1=downtrend)
            last_signal: Last known signal state
        
        Returns:
            "long" for uptrend flip, "short" for downtrend flip, None for no flip
        """
        # First run - no flip, just store state
        if last_signal is None:
            logger.info(f"📍 Initial Perusu state: {'Uptrend' if current_signal == 1 else 'Downtrend'}")
            return None
        
        # No change - NO ENTRY
        if current_signal == last_signal:
            logger.debug(f"🔄 Perusu NO flip: {current_signal} == {last_signal}")
            return None
        
        # Signal flipped!
        if current_signal == 1 and last_signal == -1:
            logger.info(f"✅ Perusu FLIP: Downtrend → Uptrend (LONG entry signal)")
            return "long"
        elif current_signal == -1 and last_signal == 1:
            logger.info(f"✅ Perusu FLIP: Uptrend → Downtrend (SHORT entry signal)")
            return "short"
        
        logger.warning(f"⚠️ Unknown signal transition: {last_signal} → {current_signal}")
        return None
    
    def generate_entry_signal(self, algo_setup_id: str,
                             last_perusu_signal: Optional[int],
                             indicators_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        ✅ FIXED: Generate entry signal ONLY on Perusu flip.
        
        CRITICAL: Entry ONLY triggers when:
        ✅ Perusu(20,20) FLIPS (uptrend or downtrend)
        ❌ NOT when Sirusu changes
        ❌ NOT when Perusu is at same level
        
        Args:
            algo_setup_id: Algo setup ID
            last_perusu_signal: Last known Perusu signal (1, -1, or None)
            indicators_data: Dict from calculate_indicators()
        
        Returns:
            Entry signal dict, or None if no Perusu flip
        """
        try:
            perusu = indicators_data.get("perusu")
            sirusu = indicators_data.get("sirusu")
            current_price = indicators_data.get("current_price")
        
            if not perusu or not sirusu or current_price is None:
                logger.error("❌ Missing indicator data for entry signal")
                return None
        
            current_perusu_signal = perusu.get("signal")
            
            # ✅ CRITICAL CHECK: Did Perusu ACTUALLY FLIP?
            entry_side = self.detect_signal_flip(current_perusu_signal, last_perusu_signal)
        
            if not entry_side:
                # ❌ NO PERUSU FLIP = NO ENTRY SIGNAL
                # (Even if Sirusu changed, even if we're in a trend)
                logger.debug(f"❌ No Perusu flip detected - NO ENTRY SIGNAL")
                logger.debug(f"   Last Perusu: {last_perusu_signal}, Current: {current_perusu_signal}")
                return None
        
            # ✅ PERUSU FLIPPED! Generate entry signal
            logger.info(f"🎯 ENTRY SIGNAL GENERATED (Perusu flip detected):")
            logger.info(f"   Entry Side: {entry_side.upper()}")
            logger.info(f"   Current Price: ${current_price:.5f}")
            logger.info(f"   Perusu Value: ${perusu['supertrend_value']:.5f}")
            logger.info(f"   Sirusu SL Level: ${sirusu['supertrend_value']:.5f}")
        
            return {
                "side": entry_side,
                "trigger_price": current_price,
                "immediate": True,  # Market entry
                "perusu_signal": current_perusu_signal,
                "perusu_value": perusu['supertrend_value'],
                "sirusu_sl": sirusu['supertrend_value'],  # For SL only!
                "entry_reason": f"Perusu(20,20) flip to {'uptrend' if entry_side == 'long' else 'downtrend'}"
            }
        
        except Exception as e:
            logger.error(f"❌ Exception generating entry signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def generate_exit_signal(self, algo_setup_id: str,
                            position_side: str,
                            indicators_data: Dict[str, Any]) -> bool:
        """
        ✅ FIXED: Generate exit signal based on Sirusu flip ONLY.
        
        Exit Logic:
        - LONG position → Exit when Sirusu flips to Downtrend
        - SHORT position → Exit when Sirusu flips to Uptrend
        
        Args:
            algo_setup_id: Algo setup ID
            position_side: Current position ("long" or "short")
            indicators_data: Dict from calculate_indicators()
        
        Returns:
            True if should exit, False otherwise
        """
        try:
            sirusu = indicators_data.get("sirusu")
            
            if not sirusu:
                logger.error("❌ Missing Sirusu data for exit signal")
                return False
            
            current_sirusu_signal = sirusu.get("signal")
            position_side_lower = position_side.lower()
            
            # ✅ Check exit conditions
            should_exit = False
            
            if position_side_lower == "long":
                # LONG → Exit when Sirusu flips to DOWNTREND (-1)
                if current_sirusu_signal == -1:
                    logger.info(f"✅ EXIT SIGNAL: Sirusu flipped to Downtrend (Close LONG)")
                    should_exit = True
                else:
                    logger.debug(f"🔄 HOLD: Sirusu still Uptrend (Keep LONG)")
            
            elif position_side_lower == "short":
                # SHORT → Exit when Sirusu flips to UPTREND (+1)
                if current_sirusu_signal == 1:
                    logger.info(f"✅ EXIT SIGNAL: Sirusu flipped to Uptrend (Close SHORT)")
                    should_exit = True
                else:
                    logger.debug(f"🔄 HOLD: Sirusu still Downtrend (Keep SHORT)")
            
            if should_exit:
                logger.info(f"🚪 Exit signal generated:")
                logger.info(f"   Position: {position_side_lower.upper()}")
                logger.info(f"   Sirusu(10,10): {'Uptrend' if current_sirusu_signal == 1 else 'Downtrend'}")
                logger.info(f"   Sirusu Value: ${sirusu['supertrend_value']:.5f}")
            
            return should_exit
            
        except Exception as e:
            logger.error(f"❌ Exception generating exit signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
