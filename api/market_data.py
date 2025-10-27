"""Market data operations - products, tickers, candles."""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from api.delta_client import DeltaExchangeClient
from config.constants import TIMEFRAME_MAPPING

logger = logging.getLogger(__name__)

# Cache for products
_products_cache: Optional[List[Dict[str, Any]]] = None
_products_cache_time: Optional[datetime] = None
_cache_expiry_seconds = 86400  # 24 hours


async def get_products(client: DeltaExchangeClient, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    """
    Get all available products (with caching).
    
    Args:
        client: Delta Exchange client instance
        force_refresh: Force refresh cache
    
    Returns:
        List of products or None
    """
    global _products_cache, _products_cache_time
    
    try:
        # Check cache
        if not force_refresh and _products_cache and _products_cache_time:
            if (datetime.utcnow() - _products_cache_time).seconds < _cache_expiry_seconds:
                logger.info("✅ Using cached products data")
                return _products_cache
        
        # Fetch fresh data
        response = await client.get("/v2/products")
        
        if response and response.get("success"):
            products = response.get("result", [])
            _products_cache = products
            _products_cache_time = datetime.utcnow()
            logger.info(f"✅ Retrieved {len(products)} products")
            return products
        
        logger.error(f"❌ Failed to get products: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting products: {e}")
        return None


async def get_product_by_symbol(client: DeltaExchangeClient, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get product details by symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol (e.g., "BTCUSD")
    
    Returns:
        Product details or None
    """
    try:
        products = await get_products(client)
        
        if not products:
            return None
        
        for product in products:
            if product.get("symbol") == symbol:
                logger.info(f"✅ Found product: {symbol} (ID: {product.get('id')})")
                return product
        
        logger.warning(f"⚠️ Product not found: {symbol}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting product by symbol: {e}")
        return None


async def get_ticker(client: DeltaExchangeClient, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get current ticker data for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
    
    Returns:
        Ticker data or None
    """
    try:
        response = await client.get("/v2/tickers", params={"symbol": symbol})
        
        if response and response.get("success"):
            tickers = response.get("result", [])
            if tickers:
                ticker = tickers[0]
                logger.info(f"✅ Got ticker for {symbol}: ${ticker.get('close')}")
                return ticker
        
        logger.error(f"❌ Failed to get ticker for {symbol}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting ticker: {e}")
        return None


async def get_candles(client: DeltaExchangeClient, symbol: str, timeframe: str,
                     start_time: Optional[int] = None, end_time: Optional[int] = None,
                     limit: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """
    Get historical OHLC candle data (auto-scaled by timeframe).
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
        timeframe: Timeframe (1m, 5m, 15m, 30m, 1h, 4h, 1d)
        start_time: Start timestamp (Unix seconds)
        end_time: End timestamp (Unix seconds)
        limit: Number of candles (auto-scaled if None)
    
    Returns:
        List of candle data or None
    """
    try:
        resolution = TIMEFRAME_MAPPING.get(timeframe, "15m")
        
        # Auto-scale candles based on timeframe if not specified
        if limit is None:
            timeframe_candle_count = {
                "1m": 100,
                "5m": 150,
                "15m": 150,
                "30m": 200,
                "1h": 250,
                "4h": 400,  # ← Key change!
                "1d": 500
            }
            limit = timeframe_candle_count.get(timeframe, 200)
        
        # Calculate start and end times if not provided
        if not end_time:
            end_time = int(datetime.utcnow().timestamp())
        
        if not start_time:
            # Calculate based on timeframe and limit
            timeframe_seconds = {
                "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400
            }
            seconds_per_candle = timeframe_seconds.get(timeframe, 900)
            
            # Add 10% buffer to ensure we get enough data
            actual_limit = int(limit * 1.1)
            start_time = end_time - (seconds_per_candle * actual_limit)
        
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": str(start_time),
            "end": str(end_time)
        }
        
        logger.info(f"🔍 Requesting {limit} candles for {symbol} ({timeframe})")
        logger.info(f"   Time range: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M')} to {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M')}")
        
        response = await client.get("/v2/history/candles", params=params)
        
        if response and response.get("success"):
            candles = response.get("result", [])
            
            # Convert to more usable format
            formatted_candles = []
            for candle in candles:
                formatted_candles.append({
                    "time": candle.get("time"),
                    "open": float(candle.get("open", 0)),
                    "high": float(candle.get("high", 0)),
                    "low": float(candle.get("low", 0)),
                    "close": float(candle.get("close", 0)),
                    "volume": float(candle.get("volume", 0))
                })
            
            # Sort by time (oldest first for proper calculation)
            formatted_candles.sort(key=lambda x: x["time"])
            
            # Limit results to requested amount (from most recent)
            formatted_candles = formatted_candles[-limit:]
            
            logger.info(f"✅ Retrieved {len(formatted_candles)} candles for {symbol} ({timeframe})")
            if formatted_candles:
                logger.info(f"   Oldest: {datetime.fromtimestamp(formatted_candles[0]['time']).strftime('%Y-%m-%d %H:%M')}")
                logger.info(f"   Newest: {datetime.fromtimestamp(formatted_candles[-1]['time']).strftime('%Y-%m-%d %H:%M')}")
            
            return formatted_candles
        
        logger.error(f"❌ Failed to get candles: {response}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting candles: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
            

async def get_latest_price(client: DeltaExchangeClient, symbol: str) -> Optional[float]:
    """
    Get latest close price for a symbol.
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
    
    Returns:
        Latest price or None
    """
    try:
        ticker = await get_ticker(client, symbol)
        
        if ticker:
            price = float(ticker.get("close", 0))
            return price
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Exception getting latest price: {e}")
        return None
                                                                 
