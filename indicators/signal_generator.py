"""Signal generation logic for entry and exit."""
import logging
from typing import Optional, Dict, Any
from config.constants import (
    SIGNAL_UPTREND, SIGNAL_DOWNTREND,
    DIRECTION_BOTH, DIRECTION_LONG_ONLY, DIRECTION_SHORT_ONLY
)

logger = logging.getLogger(__name__)


class SignalGenerator:
    """Generate trade signals based on indicator values."""
    
    @staticmethod
    def should_enter_trade(perusu_signal: int, direction: str, current_position: Optional[str]) -> Optional[str]:
        """
        Determine if should enter trade based on Perusu signal and direction.
        
        Args:
            perusu_signal: Perusu indicator signal (1 for uptrend, -1 for downtrend)
            direction: Algo direction ("both", "long_only", "short_only")
            current_position: Current position ("long", "short", None)
        
        Returns:
            "long", "short", or None
        """
        # Don't enter if already in position
        if current_position:
            logger.info(f"ℹ️ Already in {current_position} position, no entry signal")
            return None
        
        # Check Perusu uptrend signal
        if perusu_signal == SIGNAL_UPTREND:
            if direction in [DIRECTION_BOTH, DIRECTION_LONG_ONLY]:
                logger.info(f"🟢 Entry signal: LONG (Perusu uptrend)")
                return "long"
            else:
                logger.info(f"⏸️ Perusu uptrend but direction is {direction}, no entry")
                return None
        
        # Check Perusu downtrend signal
        elif perusu_signal == SIGNAL_DOWNTREND:
            if direction in [DIRECTION_BOTH, DIRECTION_SHORT_ONLY]:
                logger.info(f"🔴 Entry signal: SHORT (Perusu downtrend)")
                return "short"
            else:
                logger.info(f"⏸️ Perusu downtrend but direction is {direction}, no entry")
                return None
        
        return None
    
    @staticmethod
    def should_exit_trade(sirusu_signal: int, current_position: str) -> bool:
        """
        Determine if should exit trade based on Sirusu signal.
        
        Args:
            sirusu_signal: Sirusu indicator signal (1 for uptrend, -1 for downtrend)
            current_position: Current position ("long" or "short")
        
        Returns:
            True if should exit, False otherwise
        """
        if not current_position:
            return False
        
        # Exit long position on Sirusu downtrend
        if current_position == "long" and sirusu_signal == SIGNAL_DOWNTREND:
            logger.info(f"🚪 Exit signal: Close LONG (Sirusu downtrend)")
            return True
        
        # Exit short position on Sirusu uptrend
        elif current_position == "short" and sirusu_signal == SIGNAL_UPTREND:
            logger.info(f"🚪 Exit signal: Close SHORT (Sirusu uptrend)")
            return True
        
        return False
    
    @staticmethod
    def get_stop_loss_side(position_side: str) -> str:
        """
        Get order side for stop-loss based on position.
        
        Args:
            position_side: "long" or "short"
        
        Returns:
            "sell" for long, "buy" for short
        """
        return "sell" if position_side == "long" else "buy"
      
