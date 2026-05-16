"""Market screener for fetching gainers/losers from Delta Exchange."""
import logging
from typing import List, Dict
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_all_perpetual_tickers(client: DeltaExchangeClient) -> List[Dict]:
    """
    Fetch all perpetual futures tickers from Delta Exchange.
    Returns list of tickers with 24h price change data.
    """
    try:
        response = await client._request("GET", "/v2/tickers")
        if not response or not response.get("success"):
            logger.error(f"Failed to fetch tickers: {response}")
            return []
            
        tickers = response.get("result", [])
        
        perpetual_tickers = [
            t for t in tickers 
            if t.get("contract_type") == "perpetual_futures"
        ]
        
        logger.info(f"✅ Fetched {len(perpetual_tickers)} perpetual futures tickers")
        return perpetual_tickers
        
    except Exception as e:
        logger.error(f"❌ Error fetching tickers: {e}")
        return []


async def get_top_gainers(
    client: DeltaExchangeClient,
    timeframe: str,
    top_n: int = 10
) -> List[str]:
    """Get top N gainers (highest 24h % increase)."""
    try:
        tickers = await get_all_perpetual_tickers(client)
        
        # Calculate % change for each
        asset_changes = []
        for ticker in tickers:
            symbol = ticker.get("symbol")
            if not symbol:
                continue
            
            # Use ticker's 24h change if available
            change_24h = ticker.get("mark_change_24h")
            if change_24h is not None:
                asset_changes.append({
                    "symbol": symbol,
                    "change_pct": float(change_24h)
                })
        
        # Sort descending (highest gainers first)
        asset_changes.sort(key=lambda x: x["change_pct"], reverse=True)
        
        # Get top N
        top_gainers = [a["symbol"] for a in asset_changes[:top_n]]
        
        logger.info(f"📈 Top {top_n} Gainers: {top_gainers}")
        return top_gainers
        
    except Exception as e:
        logger.error(f"❌ Error fetching top gainers: {e}")
        return []


async def get_top_losers(
    client: DeltaExchangeClient,
    timeframe: str,
    top_n: int = 10
) -> List[str]:
    """Get top N losers (highest 24h % decrease)."""
    try:
        tickers = await get_all_perpetual_tickers(client)
        
        asset_changes = []
        for ticker in tickers:
            symbol = ticker.get("symbol")
            if not symbol:
                continue
            
            change_24h = ticker.get("mark_change_24h")
            if change_24h is not None:
                asset_changes.append({
                    "symbol": symbol,
                    "change_pct": float(change_24h)
                })
        
        # Sort ascending (biggest losers first)
        asset_changes.sort(key=lambda x: x["change_pct"])
        
        top_losers = [a["symbol"] for a in asset_changes[:top_n]]
        
        logger.info(f"📉 Top {top_n} Losers: {top_losers}")
        return top_losers
        
    except Exception as e:
        logger.error(f"❌ Error fetching top losers: {e}")
        return []


async def get_all_perpetual_symbols(client: DeltaExchangeClient) -> List[str]:
    """Get all perpetual futures symbols."""
    try:
        tickers = await get_all_perpetual_tickers(client)
        symbols = [t.get("symbol") for t in tickers if t.get("symbol")]
        logger.info(f"📊 Found {len(symbols)} perpetual futures")
        return symbols
    except Exception as e:
        logger.error(f"❌ Error fetching all symbols: {e}")
        return []


async def get_top_volume(
    client: DeltaExchangeClient,
    timeframe: str,
    top_n: int = 10
) -> List[str]:
    """Get top N assets by 24h trading volume (turnover_usd)."""
    try:
        tickers = await get_all_perpetual_tickers(client)
        
        asset_volumes = []
        for ticker in tickers:
            symbol = ticker.get("symbol")
            if not symbol:
                continue
            
            turnover_usd = ticker.get("turnover_usd")
            if turnover_usd is not None:
                asset_volumes.append({
                    "symbol": symbol,
                    "volume_usd": float(turnover_usd)
                })
        
        # Sort descending (highest volume first)
        asset_volumes.sort(key=lambda x: x["volume_usd"], reverse=True)
        
        # Get top N
        top_volume = [a["symbol"] for a in asset_volumes[:top_n]]
        
        logger.info(f"📊 Top {top_n} Volume: {top_volume}")
        if asset_volumes[:top_n]:
            for a in asset_volumes[:top_n]:
                logger.debug(f"   {a['symbol']}: ${a['volume_usd']:,.0f}")
        return top_volume
        
    except Exception as e:
        logger.error(f"❌ Error fetching top volume: {e}")
        return []


async def get_top_oi(
    client: DeltaExchangeClient,
    timeframe: str,
    top_n: int = 10
) -> List[str]:
    """Get top N assets by open interest (oi_value_usd)."""
    try:
        tickers = await get_all_perpetual_tickers(client)
        
        asset_oi = []
        for ticker in tickers:
            symbol = ticker.get("symbol")
            if not symbol:
                continue
            
            oi_usd = ticker.get("oi_value_usd")
            if oi_usd is not None:
                asset_oi.append({
                    "symbol": symbol,
                    "oi_usd": float(oi_usd)
                })
        
        # Sort descending (highest OI first)
        asset_oi.sort(key=lambda x: x["oi_usd"], reverse=True)
        
        top_oi = [a["symbol"] for a in asset_oi[:top_n]]
        
        logger.info(f"🔝 Top {top_n} OI: {top_oi}")
        if asset_oi[:top_n]:
            for a in asset_oi[:top_n]:
                logger.debug(f"   {a['symbol']}: ${a['oi_usd']:,.0f}")
        return top_oi
        
    except Exception as e:
        logger.error(f"❌ Error fetching top OI: {e}")
        return []


async def get_assets_by_tag(
    client: DeltaExchangeClient,
    tag: str,
    top_n: int = 0
) -> List[str]:
    """
    Get perpetual futures assets that have a specific tag.
    
    Delta Exchange tags include: meme, sol_ecosystem, new, ai, defi,
    layer_1, layer_2, gaming, nft, smart_contracts, metal, xStock.
    
    Assets are sorted by 24h volume (most active first).
    If top_n > 0, returns only the top N; otherwise returns all matches.
    """
    try:
        tickers = await get_all_perpetual_tickers(client)
        
        matched = []
        for ticker in tickers:
            symbol = ticker.get("symbol")
            if not symbol:
                continue
            
            tags = ticker.get("tags", [])
            if isinstance(tags, list) and tag in tags:
                turnover = float(ticker.get("turnover_usd", 0) or 0)
                matched.append({
                    "symbol": symbol,
                    "volume_usd": turnover
                })
        
        # Sort by volume descending (most active first)
        matched.sort(key=lambda x: x["volume_usd"], reverse=True)
        
        if top_n > 0:
            result = [a["symbol"] for a in matched[:top_n]]
        else:
            result = [a["symbol"] for a in matched]
        
        logger.info(f"🏷️ Tag '{tag}': {len(result)} assets found")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error fetching assets by tag '{tag}': {e}")
        return []
