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


async def get_position_by_symbol(client: DeltaExchangeClient, symbol: str, 
                                retry_count: int = 3) -> Optional[Dict[str, Any]]:
    """
    ✅ ENHANCED: Get position for a specific symbol with retry logic.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "ALGOUSD", "ADAUSD", "BTCUSD")
        retry_count: Number of retries if position not found
    
    Returns:
        Position details or None if no position
    """
    import asyncio
    
    for attempt in range(retry_count):
        try:
            logger.info(f"🔍 Looking for position: {symbol} (Attempt {attempt + 1}/{retry_count})")
            
            # ✅ Extract underlying asset from symbol
            underlying_asset = symbol.replace("USD", "").replace("USDT", "")
            
            logger.info(f"📍 Querying Delta Exchange for {underlying_asset}...")
            
            # ✅ Query specific asset with REQUIRED parameter
            response = await client.get("/v2/positions", 
                params={"underlying_asset_symbol": underlying_asset})
            
            # Log full response for debugging
            logger.debug(f"   API Response: {response}")
            
            if not response:
                logger.warning(f"⚠️ Empty response for {underlying_asset} (attempt {attempt + 1})")
                if attempt < retry_count - 1:
                    await asyncio.sleep(0.5)  # Wait 500ms before retry
                    continue
                return None
            
            if not response.get("success"):
                logger.warning(f"⚠️ API returned success=false for {underlying_asset}")
                logger.warning(f"   Full response: {response}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(0.5)
                    continue
                return None
            
            positions = response.get("result", [])
            
            if not positions:
                logger.info(f"ℹ️ No positions found for {underlying_asset} (attempt {attempt + 1})")
                if attempt < retry_count - 1:
                    await asyncio.sleep(0.5)
                    continue
                return None
            
            # Log all positions for debugging
            logger.debug(f"   Positions returned: {len(positions)}")
            for pos in positions:
                pos_symbol = pos.get("product", {}).get("symbol", "")
                pos_size = float(pos.get("size", 0))
                logger.debug(f"   - {pos_symbol}: Size={pos_size}")
            
            # Search for exact symbol match
            for position in positions:
                position_symbol = position.get("product", {}).get("symbol", "")
                position_size = float(position.get("size", 0))
                
                # Match symbol and check non-zero size
                if position_symbol == symbol and abs(position_size) > 0:
                    logger.info(f"✅ Found position for {symbol}: {position_size} contracts")
                    logger.info(f"   Entry: ${position.get('entry_price', 0)}")
                    logger.info(f"   Mark: ${position.get('mark_price', 0)}")
                    logger.info(f"   PnL: ${position.get('unrealized_pnl', 0)}")
                    return position
            
            # If not found and we have retries left
            if attempt < retry_count - 1:
                logger.warning(f"⚠️ Position {symbol} not found in API response, retrying...")
                await asyncio.sleep(0.5)
                continue
            
            # Final attempt - log all available positions
            logger.info(f"ℹ️ No open position found for {symbol} after {retry_count} attempts")
            available = [
                p.get("product", {}).get("symbol") 
                for p in positions 
                if float(p.get("size", 0)) != 0
            ]
            if available:
                logger.info(f"   Available positions: {available}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Exception getting position for {symbol} (attempt {attempt + 1}): {e}")
            if attempt < retry_count - 1:
                logger.info(f"   Retrying in 500ms...")
                await asyncio.sleep(0.5)
                continue
            
            import traceback
            logger.error(traceback.format_exc())
            return None
    
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
    
# In api/positions.py
from api.delta_client import DeltaExchangeClient

async def display_positions_for_all_apis(credentials):
    message = "📊 *Open Positions Across All APIs*\n\n"
    total_positions = 0

    for cred in credentials:
        api_name = cred.get('api_name') or cred.get('api_label') or cred.get('apikey', '')[:6] + "..."
        # Robust key fallback:
        api_key = cred.get('api_key') or cred.get('apikey') or cred.get('apiKey')
        api_secret = cred.get('api_secret') or cred.get('apisecret') or cred.get('apiSecret')
        logger.info(f"Credentials debug: {cred}")
      
        if not api_key or not api_secret:
            message += f"❌ Error fetching for {api_name}: missing API key or secret\n\n"
            continue
        try:
            client = DeltaExchangeClient(api_key, api_secret)
            positions = await get_all_positions_for_assets(client)
            await client.close()
            formatted = await format_positions_display(positions)
            message += f"=== Account: **{api_name}** ===\n"
            if not formatted:
                message += "No open positions.\n\n"
                continue
            for pos in formatted:
                message += (
                    f"• {pos['symbol']} ({pos['side']}) | Size: {pos['size']} | "
                    f"Entry: ${pos['entry_price']} | Mark: ${pos['current_price']}\n"
                    f"  Margin: ${pos['margin']} (₹{pos['margin_inr']})\n"
                    f"  PnL: ${pos['pnl']} (₹{pos['pnl_inr']}) | %: {pos['pnl_percentage']}%\n"
                    "-------------------------\n"
                )
                total_positions += 1
            message += "\n"
        except Exception as e:
            message += f"❌ Error fetching for {api_name}: {str(e)[:40]}\n\n"

    if total_positions == 0:
        message += "ℹ️ No open positions across all accounts.\n"
    return message
