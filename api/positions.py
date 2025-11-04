"""Position management operations."""
import logging
from typing import Dict, Any, Optional, List
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_all_positions_for_assets(client: DeltaExchangeClient, 
                                       assets: List[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    ✅ CORRECTED: Get all open positions for specified assets.
    
    Delta Exchange India REQUIRES underlying_asset_symbol parameter.
    We query common assets and aggregate results.
    
    Args:
        client: Delta Exchange client instance
        assets: List of underlying assets to query (default: common trading assets)
    
    Returns:
        List of open positions or empty list
    """
    if assets is None:
        # ✅ Common assets on Delta Exchange India
        assets = ["BTC", "ETH", "SOL", "MATIC", "AVAX", "ADA", "ALGO", "DOT", "NEAR", "ARB"]
    
    try:
        all_positions = []
        
        for asset in assets:
            try:
                logger.debug(f"📍 Querying positions for {asset}...")
                
                # ✅ MUST pass underlying_asset_symbol parameter!
                response = await client.get("/v2/positions", 
                    params={"underlying_asset_symbol": asset})
                
                if response and response.get("success"):
                    positions = response.get("result", [])
                    
                    # Filter out zero-size positions
                    active_positions = [
                        p for p in positions 
                        if p.get("size") and abs(float(p.get("size", 0))) > 0
                    ]
                    
                    if active_positions:
                        logger.debug(f"   📊 Found {len(active_positions)} positions for {asset}")
                        all_positions.extend(active_positions)
                    
            except Exception as e:
                logger.debug(f"⚠️ Error querying {asset}: {e}")
                continue
        
        if all_positions:
            logger.info(f"✅ Retrieved {len(all_positions)} total open positions")
            for pos in all_positions:
                symbol = pos.get("product", {}).get("symbol", "Unknown")
                size = pos.get("size", 0)
                logger.info(f"   📊 {symbol}: {size} contracts")
        else:
            logger.info(f"ℹ️ No open positions found")
        
        return all_positions
        
    except Exception as e:
        logger.error(f"❌ Exception getting positions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


async def get_positions(client: DeltaExchangeClient) -> Optional[List[Dict[str, Any]]]:
    """
    ✅ CORRECTED: Get all open positions.
    
    Uses get_all_positions_for_assets() which queries with proper parameters.
    
    Args:
        client: Delta Exchange client instance
    
    Returns:
        List of open positions or empty list
    """
    return await get_all_positions_for_assets(client)


async def get_position_by_symbol(client: DeltaExchangeClient, symbol: str) -> Optional[Dict[str, Any]]:
    """
    ✅ CORRECTED: Get position for a specific symbol efficiently.
    
    Extracts underlying asset from symbol and queries directly.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "ALGOUSD", "ADAUSD", "BTCUSD")
    
    Returns:
        Position details or None if no position
    """
    try:
        logger.info(f"🔍 Looking for position: {symbol}")
        
        # ✅ Extract underlying asset from symbol
        # ALGOUSD -> ALGO, ADAUSD -> ADA, BTCUSD -> BTC
        underlying_asset = symbol.replace("USD", "").replace("USDT", "")
        
        logger.info(f"📍 Querying Delta Exchange for {underlying_asset}...")
        
        # ✅ Query specific asset with REQUIRED parameter
        response = await client.get("/v2/positions", 
            params={"underlying_asset_symbol": underlying_asset})
        
        if not response or not response.get("success"):
            logger.warning(f"⚠️ No positions response for {underlying_asset}")
            return None
        
        positions = response.get("result", [])
        
        if not positions:
            logger.info(f"ℹ️ No positions found for {underlying_asset}")
            return None
        
        # Search for exact symbol match
        for position in positions:
            position_symbol = position.get("product", {}).get("symbol", "")
            position_size = float(position.get("size", 0))
            
            if position_symbol == symbol and abs(position_size) > 0:
                logger.info(f"✅ Found position for {symbol}: {position_size} contracts")
                logger.info(f"   Entry: ${position.get('entry_price', 0)}")
                logger.info(f"   Mark: ${position.get('mark_price', 0)}")
                logger.info(f"   PnL: ${position.get('unrealized_pnl', 0)}")
                return position
        
        logger.info(f"ℹ️ No open position found for {symbol}")
        if positions:
            available = [p.get("product", {}).get("symbol") for p in positions if float(p.get("size", 0)) != 0]
            if available:
                logger.info(f"   Available positions: {available}")
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting position for {symbol}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def get_position_size(client: DeltaExchangeClient, symbol: str) -> float:
    """
    ✅ Get position size for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "ALGOUSD")
    
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
    ✅ Check if position is open for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
    
    Returns:
        True if position is open, False otherwise
    """
    try:
        size = await get_position_size(client, symbol)
        is_open = abs(size) > 0
        logger.info(f"📍 Position {'OPEN' if is_open else 'CLOSED'} for {symbol}")
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
    
