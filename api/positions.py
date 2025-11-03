"""Position management operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_positions(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    ✅ FIXED: Get all open positions using correct Delta Exchange API.
    
    Uses /v2/positions endpoint WITHOUT filters to get all open positions.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        List of open positions or None on failure
    """
    try:
        # ✅ FIXED: Get ALL positions without filtering
        # Delta Exchange returns all open positions when no params passed
        response = await client.get("/v2/positions")
        
        if not response or not response.get("success"):
            logger.warning(f"⚠️ No positions response: {response}")
            return []
        
        positions = response.get("result", [])
        
        # Filter out zero-size positions
        active_positions = [
            p for p in positions 
            if p.get("size") and abs(float(p.get("size", 0))) > 0
        ]
        
        logger.info(f"✅ Retrieved {len(active_positions)} open positions")
        
        for pos in active_positions:
            symbol = pos.get("product", {}).get("symbol", "Unknown")
            size = pos.get("size", 0)
            logger.info(f"   📊 {symbol}: {size} contracts")
        
        return active_positions
        
    except Exception as e:
        logger.error(f"❌ Exception getting positions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


async def get_position_by_symbol(client: DeltaExchangeClient, symbol: str) -> Optional[Dict[str, Any]]:
    """
    ✅ FIXED: Get position for a specific symbol efficiently.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "ADAUSD", "BTCUSD")
    
    Returns:
        Position details or None if no position
    """
    try:
        logger.info(f"🔍 Looking for position: {symbol}")
        
        # Get all positions
        positions = await get_positions(client)
        
        if not positions:
            logger.info(f"ℹ️ No open positions found")
            return None
        
        # Search for matching symbol
        for position in positions:
            position_symbol = position.get("product", {}).get("symbol", "")
            
            if position_symbol == symbol:
                size = position.get("size", 0)
                logger.info(f"✅ Found position for {symbol}: {size} contracts")
                return position
        
        logger.info(f"ℹ️ No open position found for {symbol}")
        logger.info(f"   Available symbols: {[p.get('product', {}).get('symbol') for p in positions]}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting position by symbol {symbol}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def get_position_size(client: DeltaExchangeClient, symbol: str) -> float:
    """
    ✅ NEW: Get position size for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "ADAUSD")
    
    Returns:
        Position size (positive=long, negative=short, 0=no position)
    """
    try:
        position = await get_position_by_symbol(client, symbol)
        
        if not position:
            logger.info(f"📍 Position size for {symbol}: 0 (no position)")
            return 0.0
        
        size = float(position.get("size", 0))
        logger.info(f"📍 Position size for {symbol}: {size}")
        return size
        
    except Exception as e:
        logger.error(f"❌ Error getting position size: {e}")
        return 0.0


async def is_position_open(client: DeltaExchangeClient, symbol: str) -> bool:
    """
    ✅ NEW: Check if position is open for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
    
    Returns:
        True if position is open, False otherwise
    """
    try:
        size = await get_position_size(client, symbol)
        is_open = abs(size) > 0
        logger.info(f"📍 Position open for {symbol}: {is_open}")
        return is_open
        
    except Exception as e:
        logger.error(f"❌ Error checking if position is open: {e}")
        return False


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
            size = float(pos.get("size", 0))
            
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
                "entry_price": round(entry_price, 5),
                "current_price": round(current_price, 5),
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
    
