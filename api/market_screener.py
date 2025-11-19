"""Market screener for fetching gainers/losers from Delta Exchange."""
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


async def get_all_perpetual_tickers(client: DeltaExchangeClient) -> List[Dict]:
    """
    Fetch all perpetual futures tickers from Delta Exchange.
    
    Returns list of tickers with 24h price change data.
    """
    try:
        # Delta Exchange API endpoint for tickers
        url = f"{client.base_url}/v2/tickers"
        headers = client._get_headers()
        
        response = await client.session.get(url, headers=headers)
        data = await response.json()
        
        if not data.get("success"):
            logger.error(f"Failed to fetch tickers: {data}")
            return []
        
        tickers = data.get("result", [])
        
        # Filter only perpetual futures (contract_type = "perpetual_futures")
        perpetual_tickers = [
            t for t in tickers 
            if t.get("contract_type") == "perpetual_futures"
        ]
        
        logger.info(f"✅ Fetched {len(perpetual_tickers)} perpetual futures tickers")
        return perpetual_tickers
        
    except Exception as e:
        logger.error(f"❌ Error fetching tickers: {e}")
        return []


async def calculate_percentage_change_24h(
    client: DeltaExchangeClient,
    symbol: str,
    timeframe: str
) -> Optional[float]:
    """
    Calculate 24-hour percentage change aligned to timeframe boundary.
    
    Example: If current time is 5:30 PM IST on Nov 19,
    compares with 5:30 PM IST on Nov 18.
    """
    try:
        from api.market_data import get_candles
        from utils.timeframe import get_timeframe_seconds
        
        current_time = datetime.utcnow()
        timeframe_seconds = get_timeframe_seconds(timeframe)
        
        # Get current boundary-aligned time
        boundary_time = current_time.replace(second=0, microsecond=0)
        
        # Calculate 24h ago at same boundary
        time_24h_ago = boundary_time - timedelta(hours=24)
        
        # Fetch 2 candles: one from 24h ago, one current
        start_time = int(time_24h_ago.timestamp())
        end_time = int(boundary_time.timestamp())
        
        candles = await get_candles(
            client, symbol, timeframe,
            start_time=start_time,
            end_time=end_time,
            limit=50  # Get enough candles
        )
        
        if not candles or len(candles) < 2:
            return None
        
        # First candle (24h ago) and last candle (current)
        old_price = float(candles[0]['close'])
        new_price = float(candles[-1]['close'])
        
        if old_price == 0:
            return None
        
        percent_change = ((new_price - old_price) / old_price) * 100
        return round(percent_change, 2)
        
    except Exception as e:
        logger.error(f"❌ Error calculating % change for {symbol}: {e}")
        return None


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
            change_24h = ticker.get("change_24h")
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
            
            change_24h = ticker.get("change_24h")
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
