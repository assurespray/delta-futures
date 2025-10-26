"""Data fetching service for historical and live data."""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles
from config.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)


class DataFetcher:
    """Service for fetching and managing market data."""
    
    def __init__(self):
        """Initialize data fetcher."""
        self.data_cache = {}  # In-memory cache for candle data
    
    async def fetch_historical_candles(self, client: DeltaExchangeClient, 
                                      symbol: str, timeframe: str,
                                      num_candles: int = 100) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch historical candles for indicator calculation.
        
        Args:
            client: Delta Exchange client
            symbol: Trading symbol
            timeframe: Timeframe
            num_candles: Number of candles to fetch
        
        Returns:
            List of candle data or None
        """
        try:
            # Calculate time range
            seconds_per_candle = TIMEFRAME_SECONDS.get(timeframe, 900)
            end_time = int(datetime.utcnow().timestamp())
            start_time = end_time - (seconds_per_candle * num_candles)
            
            # Fetch candles
            candles = await get_candles(
                client=client,
                symbol=symbol,
                timeframe=timeframe,
                start_time=start_time,
                end_time=end_time,
                limit=num_candles
            )
            
            if candles:
                logger.info(f"✅ Fetched {len(candles)} candles for {symbol} ({timeframe})")
                
                # Update cache
                cache_key = f"{symbol}_{timeframe}"
                self.data_cache[cache_key] = {
                    "candles": candles,
                    "updated_at": datetime.utcnow()
                }
                
                return candles
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Exception fetching historical candles: {e}")
            return None
    
    def get_cached_candles(self, symbol: str, timeframe: str, 
                          max_age_seconds: int = 300) -> Optional[List[Dict[str, Any]]]:
        """
        Get candles from cache if fresh enough.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            max_age_seconds: Maximum age of cached data in seconds
        
        Returns:
            Cached candles or None
        """
        cache_key = f"{symbol}_{timeframe}"
        
        if cache_key in self.data_cache:
            cached_data = self.data_cache[cache_key]
            age = (datetime.utcnow() - cached_data['updated_at']).total_seconds()
            
            if age < max_age_seconds:
                logger.info(f"✅ Using cached candles for {symbol} ({timeframe}), age: {age:.0f}s")
                return cached_data['candles']
        
        return None
    
    async def update_live_candle(self, client: DeltaExchangeClient, 
                                symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the latest candle for real-time updates.
        
        Args:
            client: Delta Exchange client
            symbol: Trading symbol
            timeframe: Timeframe
        
        Returns:
            Latest candle data or None
        """
        try:
            # Fetch last 2 candles
            candles = await get_candles(
                client=client,
                symbol=symbol,
                timeframe=timeframe,
                limit=2
            )
            
            if candles and len(candles) > 0:
                latest_candle = candles[-1]
                logger.info(f"✅ Latest candle for {symbol}: Close=${latest_candle['close']}")
                return latest_candle
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Exception fetching live candle: {e}")
            return None
          
