"""Market utility functions for fetching asset data."""
import logging
from typing import List, Dict, Any
import aiohttp
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_available_assets(client: DeltaExchangeClient) -> List[str]:
    """
    Get all available tradeable assets.
    
    Args:
        client: DeltaExchangeClient instance
    
    Returns:
        List of asset symbols (e.g., ['BTCUSD', 'ETHUSD', 'ADAUSD'])
    """
    try:
        # Fetch all products/contracts from Delta Exchange
        response = await client.get_products()
        
        if not response:
            logger.warning("⚠️ No products found from Delta Exchange")
            return []
        
        # Extract unique asset symbols
        assets = []
        seen = set()
        
        for product in response:
            symbol = product.get("symbol", "").upper()
            if symbol and symbol not in seen:
                assets.append(symbol)
                seen.add(symbol)
        
        logger.info(f"✅ Found {len(assets)} available assets")
        return sorted(assets)
    
    except Exception as e:
        logger.error(f"❌ Error fetching available assets: {e}")
        return []


async def get_top_gainers(client: DeltaExchangeClient, limit: int = 10) -> List[str]:
    """
    Get top gaining assets in the last 24 hours.
    
    Args:
        client: DeltaExchangeClient instance
        limit: Number of top gainers to return (default: 10)
    
    Returns:
        List of asset symbols sorted by gain percentage
    """
    try:
        # Fetch market data/24h stats
        response = await client.get_24h_stats()
        
        if not response:
            logger.warning("⚠️ No 24h stats available")
            return []
        
        # Calculate gainers and sort by percentage change
        gainers = []
        
        for product in response:
            symbol = product.get("symbol", "").upper()
            change_percent = float(product.get("change_24h_percent", 0))
            
            if symbol and change_percent > 0:
                gainers.append({
                    "symbol": symbol,
                    "change": change_percent
                })
        
        # Sort by change percentage (descending) and get top N
        gainers = sorted(gainers, key=lambda x: x["change"], reverse=True)[:limit]
        result = [g["symbol"] for g in gainers]
        
        logger.info(f"✅ Found {len(result)} top gainers")
        return result
    
    except Exception as e:
        logger.error(f"❌ Error fetching top gainers: {e}")
        return []


async def get_top_losers(client: DeltaExchangeClient, limit: int = 10) -> List[str]:
    """
    Get top losing assets in the last 24 hours.
    
    Args:
        client: DeltaExchangeClient instance
        limit: Number of top losers to return (default: 10)
    
    Returns:
        List of asset symbols sorted by loss percentage
    """
    try:
        # Fetch market data/24h stats
        response = await client.get_24h_stats()
        
        if not response:
            logger.warning("⚠️ No 24h stats available")
            return []
        
        # Calculate losers and sort by percentage change
        losers = []
        
        for product in response:
            symbol = product.get("symbol", "").upper()
            change_percent = float(product.get("change_24h_percent", 0))
            
            if symbol and change_percent < 0:
                losers.append({
                    "symbol": symbol,
                    "change": change_percent
                })
        
        # Sort by change percentage (ascending - most negative first) and get top N
        losers = sorted(losers, key=lambda x: x["change"])[:limit]
        result = [l["symbol"] for l in losers]
        
        logger.info(f"✅ Found {len(result)} top losers")
        return result
    
    except Exception as e:
        logger.error(f"❌ Error fetching top losers: {e}")
        return []


async def get_market_data(
    client: DeltaExchangeClient,
    symbol: str
) -> Dict[str, Any]:
    """
    Get current market data for an asset.
    
    Args:
        client: DeltaExchangeClient instance
        symbol: Asset symbol
    
    Returns:
        Dict with price, volume, change data
    """
    try:
        response = await client.get_ticker(symbol)
        
        if not response:
            logger.warning(f"⚠️ No market data for {symbol}")
            return {}
        
        return {
            "symbol": symbol,
            "price": float(response.get("last_price", 0)),
            "volume_24h": float(response.get("volume_24h", 0)),
            "change_24h_percent": float(response.get("change_24h_percent", 0)),
            "high_24h": float(response.get("high_24h", 0)),
            "low_24h": float(response.get("low_24h", 0))
        }
    
    except Exception as e:
        logger.error(f"❌ Error fetching market data for {symbol}: {e}")
        return {}
          
