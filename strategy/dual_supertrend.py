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
    Dual SuperTrend breakout + trailing stop strategy.
    
    Entry Logic:
    - Perusu (20,20) signal flip triggers breakout entry order
    - Entry at previous candle HIGH/LOW + 1 pip (stop-market order)
    
    Exit Logic:
    - Sirusu (10,10) signal flip triggers market exit
    - Optional: Sirusu value used as stop-loss (additional protection)
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
            timeframe: Timeframe string (e.g., "15m")
        
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
            
            # Get previous candle high/low for breakout entry
            if len(candles) >= 2:
                prev_candle = candles[-2]  # Previous closed candle
                prev_high = float(prev_candle.get("high", 0))
                prev_low = float(prev_candle.get("low", 0))
            else:
                prev_high = float(candles[-1].get("high", 0))
                prev_low = float(candles[-1].get("low", 0))
            
            logger.info(f"📊 {symbol} {timeframe} - Indicators calculated")
            logger.info(f"   Perusu: {perusu_result['signal_text']}, Value: ${perusu_result['supertrend_value']:.5f}")
            logger.info(f"   Sirusu: {sirusu_result['signal_text']}, Value: ${sirusu_result['supertrend_value']:.5f}")
            logger.info(f"   Previous candle: High ${prev_high:.5f}, Low ${prev_low:.5f}")
            
            return {
                "perusu": perusu_result,
                "sirusu": sirusu_result,
                "previous_candle_high": prev_high,
                "previous_candle_low": prev_low,
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
    
    # ✅ FIXED METHOD SIGNATURES (added 'setup' parameter)
    def generate_entry_signal(self, setup: Dict[str, Any], 
                             indicators_data: Dict[str, Any],
                             last_perusu_signal: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Generate entry signal based on Perusu flip + breakout logic.
        
        Args:
            setup: Algo setup dictionary (from database)
            indicators_data: Dict from calculate_indicators()
            last_perusu_signal: Last known Perusu signal state
        
        Returns:
            Entry signal dict or None
        """
        try:
            perusu = indicators_data.get("perusu")
            prev_high = indicators_data.get("previous_candle_high")
            prev_low = indicators_data.get("previous_candle_low")
            
            if not perusu or not prev_high or not prev_low:
                logger.error("❌ Missing indicator data for entry signal")
                return None
            
            current_signal = perusu.get("signal")
            
            # Detect signal flip
            entry_side = self.detect_signal_flip(current_signal, last_perusu_signal)
            
            if not entry_side:
                # No flip detected
                return None
            
            # Calculate breakout trigger price
            breakout_price = self.calculate_breakout_price(entry_side, prev_high, prev_low)
            
            logger.info(f"🎯 Entry signal generated:")
            logger.info(f"   Side: {entry_side.upper()}")
            logger.info(f"   Breakout trigger: ${breakout_price:.5f}")
            logger.info(f"   Perusu value: ${perusu['supertrend_value']:.5f}")
            
            return {
                "side": entry_side,
                "trigger_price": breakout_price,
                "perusu_signal": current_signal,
                "perusu_value": perusu['supertrend_value'],
                "prev_high": prev_high,
                "prev_low": prev_low
            }
            
        except Exception as e:
            logger.error(f"❌ Exception generating entry signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def generate_exit_signal(self, setup: Dict[str, Any],
                            indicators_data: Dict[str, Any],
                            position_side: str) -> bool:
        """
        Generate exit signal based on Sirusu flip.
        
        Args:
            setup: Algo setup dictionary (from database)
            indicators_data: Dict from calculate_indicators()
            position_side: Current position ("long" or "short")
        
        Returns:
            True if should exit, False otherwise
        """
        try:
            sirusu = indicators_data.get("sirusu")
            
            if not sirusu:
                logger.error("❌ Missing Sirusu data for exit signal")
                return False
            
            current_signal = sirusu.get("signal")
            
            should_exit = self.should_exit_position(current_signal, position_side)
            
            if should_exit:
                logger.info(f"🚪 Exit signal generated:")
                logger.info(f"   Position: {position_side.upper()}")
                logger.info(f"   Sirusu signal: {'Uptrend' if current_signal == 1 else 'Downtrend'}")
                logger.info(f"   Sirusu value: ${sirusu['supertrend_value']:.5f}")
            
            return should_exit
            
        except Exception as e:
            logger.error(f"❌ Exception generating exit signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
