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
            # Scale candle requirements by timeframe
            timeframe_requirements = {
                "1m": 300,
                "5m": 350,
                "15m": 350,
                "30m": 350,
                "1h": 400,
                "4h": 500,
                "1d": 700  # Daily needs ~500 candles for ATR(20) accuracy
            }
    
            required_candles = timeframe_requirements.get(timeframe, 200)
            
            logger.info(f"📊 Fetching {required_candles} candles for {symbol} ({timeframe})")
            candles = await get_candles(client, symbol, timeframe, limit=required_candles)
        
            if not candles:
                logger.error(f"❌ Failed to fetch candles for {symbol}")
                return None
            
            actual_count = len(candles)
            logger.info(f"✅ Retrieved {actual_count} candles for {symbol}")
            
            # Check if we have minimum required data
            # Need at least ATR period + 10 buffer
            min_required = max(PERUSU_ATR_LENGTH, SIRUSU_ATR_LENGTH) + 10
            if actual_count < min_required:
                logger.error(f"❌ Insufficient data: got {actual_count}, need at least {min_required}")
                return None
            
            if actual_count < required_candles:
                logger.warning(f"⚠️ Got {actual_count} candles, wanted {required_candles} - may affect accuracy")
        
            # Calculate Perusu with detailed logging
            logger.info(f"🔵 Calculating Perusu (ATR {PERUSU_ATR_LENGTH}, Factor {PERUSU_FACTOR}) with {actual_count} candles...")
            perusu_result = self.perusu.calculate(candles)
            if not perusu_result:
                logger.error(f"❌ Failed to calculate Perusu for {symbol}")
                return None
        
            # Calculate Sirusu with detailed logging
            logger.info(f"🔴 Calculating Sirusu (ATR {SIRUSU_ATR_LENGTH}, Factor {SIRUSU_FACTOR}) with {actual_count} candles...")
            sirusu_result = self.sirusu.calculate(candles)
            if not sirusu_result:
                logger.error(f"❌ Failed to calculate Sirusu for {symbol}")
                return None
        
            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "calculated_at": datetime.utcnow(),
                "candles_used": actual_count,
                "perusu": perusu_result,
                "sirusu": sirusu_result
            }
        
            logger.info(f"✅ Indicators calculated for {symbol} ({timeframe})")
            logger.info(f"   📊 Candles used: {actual_count}")
            logger.info(f"   🔵 Perusu: {perusu_result['signal_text']} @ ${perusu_result['supertrend_value']:,.2f}")
            logger.info(f"   🔴 Sirusu: {sirusu_result['signal_text']} @ ${sirusu_result['supertrend_value']:,.2f}")
        
            return result
        
        except Exception as e:
            logger.error(f"❌ Exception calculating indicators: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
        return sirusu_value
        
