"""Dual SuperTrend strategy implementation (Perusu + Sirusu)."""
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
    TIMEFRAME_SECONDS
)

logger = logging.getLogger(__name__)


class DualSuperTrendStrategy:
    """Dual SuperTrend strategy with Perusu (entry) and Sirusu (exit)."""
    
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
    
    async def calculate_indicators(self, client: DeltaExchangeClient, symbol: str, 
                               timeframe: str) -> Optional[Dict[str, Any]]:
        """
        Calculate both Perusu and Sirusu indicators.
    
        Args:
            client: Delta Exchange client
            symbol: Trading symbol
            timeframe: Timeframe for calculation
    
        Returns:
            Dictionary with both indicator results or None
        """
        try:
            # Fetch candles (need enough for longest ATR period + buffer)
            # Perusu needs 20 periods, so fetch 50 to be safe
            required_candles = max(PERUSU_ATR_LENGTH, SIRUSU_ATR_LENGTH) + 30
            candles = await get_candles(client, symbol, timeframe, limit=required_candles)
        
            if not candles or len(candles) < required_candles:
                logger.warning(f"⚠️ Insufficient candle data for {symbol}: got {len(candles) if candles else 0}, need {required_candles}")
                return None
        
            # Calculate Perusu
            perusu_result = self.perusu.calculate(candles)
            if not perusu_result:
                logger.error(f"❌ Failed to calculate Perusu for {symbol}")
                return None
        
            # Calculate Sirusu
            sirusu_result = self.sirusu.calculate(candles)
            if not sirusu_result:
                logger.error(f"❌ Failed to calculate Sirusu for {symbol}")
                return None
        
            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "calculated_at": datetime.utcnow(),
                "perusu": perusu_result,
                "sirusu": sirusu_result
            }
        
            logger.info(f"✅ Indicators calculated for {symbol} ({timeframe})")
            logger.info(f"   Perusu: {perusu_result['signal_text']} @ ${perusu_result['supertrend_value']}")
            logger.info(f"   Sirusu: {sirusu_result['signal_text']} @ ${sirusu_result['supertrend_value']}")
        
            return result
        
        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            return None

    def generate_entry_signal(self, perusu_signal: int, direction: str, 
                             current_position: Optional[str]) -> Optional[str]:
        """
        Generate entry signal based on Perusu.
        
        Args:
            perusu_signal: Perusu signal value
            direction: Algo direction setting
            current_position: Current position if any
        
        Returns:
            "long", "short", or None
        """
        return self.signal_generator.should_enter_trade(perusu_signal, direction, current_position)
    
    def generate_exit_signal(self, sirusu_signal: int, current_position: str) -> bool:
        """
        Generate exit signal based on Sirusu.
        
        Args:
            sirusu_signal: Sirusu signal value
            current_position: Current position
        
        Returns:
            True if should exit, False otherwise
        """
        return self.signal_generator.should_exit_trade(sirusu_signal, current_position)
    
    def get_stop_loss_price(self, sirusu_value: float, position_side: str) -> float:
        """
        Get stop-loss price based on Sirusu value.
        
        Args:
            sirusu_value: Sirusu SuperTrend line value
            position_side: "long" or "short"
        
        Returns:
            Stop-loss price
        """
        # For long positions, stop-loss is below (Sirusu lower band)
        # For short positions, stop-loss is above (Sirusu upper band)
        # The Sirusu value already represents the appropriate band
        return sirusu_value
                               
