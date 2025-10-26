"""Risk management utilities."""
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class RiskManager:
    """Risk management for trading operations."""
    
    @staticmethod
    def validate_trade_size(lot_size: int, available_balance: float, 
                           price: float, min_size: int = 1, max_size: int = 10000) -> bool:
        """
        Validate if trade size is within acceptable limits.
        
        Args:
            lot_size: Requested lot size
            available_balance: Available account balance
            price: Current price
            min_size: Minimum allowed size
            max_size: Maximum allowed size
        
        Returns:
            True if valid, False otherwise
        """
        if lot_size < min_size:
            logger.warning(f"⚠️ Lot size {lot_size} below minimum {min_size}")
            return False
        
        if lot_size > max_size:
            logger.warning(f"⚠️ Lot size {lot_size} above maximum {max_size}")
            return False
        
        # Estimate required margin (simplified)
        estimated_margin = (price * lot_size) * 0.1  # Assuming 10x leverage
        
        if estimated_margin > available_balance * 0.8:  # Use max 80% of balance
            logger.warning(f"⚠️ Trade size too large for available balance")
            return False
        
        return True
    
    @staticmethod
    def calculate_position_risk(entry_price: float, stop_loss_price: float,
                               lot_size: int, position_side: str) -> Dict[str, float]:
        """
        Calculate position risk metrics.
        
        Args:
            entry_price: Entry price
            stop_loss_price: Stop-loss price
            lot_size: Position size
            position_side: "long" or "short"
        
        Returns:
            Dictionary with risk metrics
        """
        if position_side == "long":
            risk_per_contract = entry_price - stop_loss_price
        else:
            risk_per_contract = stop_loss_price - entry_price
        
        total_risk = abs(risk_per_contract * lot_size)
        risk_percentage = abs((risk_per_contract / entry_price) * 100)
        
        return {
            "risk_per_contract": round(risk_per_contract, 2),
            "total_risk_usd": round(total_risk, 2),
            "total_risk_inr": round(total_risk * 85, 2),
            "risk_percentage": round(risk_percentage, 2)
        }
                                 
