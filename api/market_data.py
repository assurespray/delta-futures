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
                return _products_cache
        
        # Fetch fresh data
        response = await client.get("/v2/products")
        
        if response and response.get("success"):
            products = response.get("result", [])
            _products_cache = products
            _products_cache_time = datetime.utcnow()
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
    
    ✅ COMPLETE: Supports ALL Delta Exchange timeframes
    
    Args:
        client: Delta Exchange client instance
        symbol: Trading symbol
        timeframe: Timeframe (1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, etc.)
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
            # ✅ COMPLETE: ALL timeframes with optimized candle counts
            timeframe_candle_count = {
                # ===== MINUTES =====
                "1m": 450,
                "2m": 300,
                "3m": 200,      # ← FIXED: Reduced from 450
                "4m": 225,
                "5m": 200,      # ← REDUCED
                "10m": 150,
                "15m": 150,     # ← REDUCED from 450
                "20m": 135,
                "30m": 120,     # ← REDUCED from 450
                "45m": 100,
                
                # ===== HOURS =====
                "1h": 100,
                "2h": 75,
                "3h": 60,
                "4h": 60,       # ← REDUCED from 800
                "6h": 50,
                "8h": 40,
                "12h": 30,
                
                # ===== DAYS =====
                "1d": 50,       # ← REDUCED from 1000
                "2d": 40,
                "3d": 30,
                "7d": 25,
                "1w": 25,
            }
            limit = timeframe_candle_count.get(timeframe, 150)
        
        # Calculate start and end times if not provided
        if not end_time:
            end_time = int(datetime.utcnow().timestamp())
        
        if not start_time:
            # ✅ COMPLETE: ALL timeframes with correct second calculations
            timeframe_seconds = {
                # ===== MINUTES =====
                "1m": 60,
                "2m": 120,
                "3m": 180,      # ← CRITICAL: NOW INCLUDED!
                "4m": 240,
                "5m": 300,
                "10m": 600,
                "15m": 900,
                "20m": 1200,
                "30m": 1800,
                "45m": 2700,
                
                # ===== HOURS =====
                "1h": 3600,
                "2h": 7200,
                "3h": 10800,
                "4h": 14400,
                "6h": 21600,
                "8h": 28800,
                "12h": 43200,
                
                # ===== DAYS =====
                "1d": 86400,
                "2d": 172800,
                "3d": 259200,
                "7d": 604800,
                "1w": 604800,
            }
            
            seconds_per_candle = timeframe_seconds.get(timeframe)
            
            # ✅ SAFETY CHECK: Ensure timeframe exists
            if seconds_per_candle is None:
                logger.error(f"❌ Unknown timeframe: {timeframe}")
                return None
            
            # Add 10% buffer to ensure we get enough data
            actual_limit = int(limit * 1.1)
            start_time = end_time - (seconds_per_candle * actual_limit)
        
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": str(start_time),
            "end": str(end_time)
        }
        
        response = await client.get("/v2/history/candles", params=params)
        
        if response and response.get("success"):
            candles = response.get("result", [])
            
            if not candles:
                logger.error(f"❌ No candles returned for {symbol} {timeframe}")
                return None
        
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
            
