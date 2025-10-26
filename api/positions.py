"""Position management operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_positions(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    Get all open positions.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        List of open positions or None on failure
    """
    try:
        response = await client.get("/v2/positions")
        
        if response and response.get("success"):
            positions = response.get("result", [])
            logger.info(f"✅ Retrieved {len(positions)} open positions")
            return positions
        
        logger.error(f"❌ Failed to get positions: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting positions: {e}")
        return None


async def get_position_by_symbol(client: DeltaExchangeClient, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get position for a specific symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "BTCUSD")
    
    Returns:
        Position details or None
    """
    try:
        positions = await get_positions(client)
        
        if not positions:
            return None
        
        for position in positions:
            if position.get("product", {}).get("symbol") == symbol:
                return position
        
        logger.info(f"ℹ️ No open position found for {symbol}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting position by symbol: {e}")
        return None


async def format_positions_display(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format positions for display in Telegram.
    
    Args:
        positions: List of raw position data
    
    Returns:
        List of formatted position data
    """
    formatted = []
    
    for pos in positions:
        try:
            product = pos.get("product", {})
            symbol = product.get("symbol", "Unknown")
            size = int(pos.get("size", 0))
            
            # Skip if no position
            if size == 0:
                continue
            
            entry_price = float(pos.get("entry_price", 0))
            current_price = float(pos.get("mark_price", 0))
            margin = float(pos.get("margin", 0))
            pnl = float(pos.get("unrealized_pnl", 0))
            pnl_percentage = float(pos.get("unrealized_pnl_percentage", 0))
            
            formatted_pos = {
                "symbol": symbol,
                "size": size,
                "side": "Long" if size > 0 else "Short",
                "entry_price": round(entry_price, 2),
                "current_price": round(current_price, 2),
                "margin": round(margin, 2),
                "margin_inr": round(margin * 85, 2),
                "pnl": round(pnl, 2),
                "pnl_inr": round(pnl * 85, 2),
                "pnl_percentage": round(pnl_percentage, 2)
            }
            
            formatted.append(formatted_pos)
            
        except Exception as e:
            logger.error(f"❌ Error formatting position: {e}")
            continue
    
    return formatted
  
