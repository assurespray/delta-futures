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
    def should_enter_trade(primary_signal: int, direction: str, current_position: Optional[str]) -> Optional[str]:
        """
        Determine if should enter trade based on primary indicator signal and direction.
        
        Args:
            primary_signal: Primary indicator signal (1 for uptrend, -1 for downtrend)
            direction: Algo direction ("both", "long_only", "short_only")
            current_position: Current position ("long", "short", None)
        
        Returns:
            "long", "short", or None
        """
        # Don't enter if already in position
        if current_position:
            logger.info(f"ℹ️ Already in {current_position} position, no entry signal")
            return None
        
        # Check primary uptrend signal
        if primary_signal == SIGNAL_UPTREND:
            if direction in [DIRECTION_BOTH, DIRECTION_LONG_ONLY]:
                logger.info(f"🟢 Entry signal: LONG (Primary uptrend)")
                return "long"
            else:
                logger.info(f"⏸️ Primary uptrend but direction is {direction}, no entry")
                return None
        
        # Check primary downtrend signal
        elif primary_signal == SIGNAL_DOWNTREND:
            if direction in [DIRECTION_BOTH, DIRECTION_SHORT_ONLY]:
                logger.info(f"🔴 Entry signal: SHORT (Primary downtrend)")
                return "short"
            else:
                logger.info(f"⏸️ Primary downtrend but direction is {direction}, no entry")
                return None
        
        return None
    
    @staticmethod
    def should_exit_trade(secondary_signal: int, current_position: str) -> bool:
        """
        Determine if should exit trade based on secondary indicator signal.
        
        Args:
            secondary_signal: Secondary indicator signal (1 for uptrend, -1 for downtrend)
            current_position: Current position ("long" or "short")
        
        Returns:
            True if should exit, False otherwise
        """
        if not current_position:
            return False
        
        # Exit long position on secondary downtrend
        if current_position == "long" and secondary_signal == SIGNAL_DOWNTREND:
            logger.info(f"🚪 Exit signal: Close LONG (Secondary downtrend)")
            return True
        
        # Exit short position on secondary uptrend
        elif current_position == "short" and secondary_signal == SIGNAL_UPTREND:
            logger.info(f"🚪 Exit signal: Close SHORT (Secondary uptrend)")
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
      
