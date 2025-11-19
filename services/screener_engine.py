"""Screener processing engine with full trading logic."""
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from database.crud import (
    get_all_active_algo_setups,
    get_all_active_screener_setups,
    get_api_credential_by_id,
    get_screener_positions_by_asset,
    get_screener_indicator_cache,
    upsert_screener_indicator_cache,
    acquire_position_lock,
    release_position_lock,
    get_position_lock
)
from api.delta_client import DeltaExchangeClient
from api.market_screener import (
    get_top_gainers,
    get_top_losers,
    get_all_perpetual_symbols
)
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.position_manager import PositionManager
from services.logger_bot import LoggerBot
from config.settings import settings

logger = logging.getLogger(__name__)


class ScreenerEngine:
    """
    Screener engine with full entry/exit/SL logic.
    
    ✅ Fetches top gainers/losers or all assets
    ✅ Filters algo assets
    ✅ First-come-first-served across screeners
    ✅ Option 1: Conservative new asset entry (wait for flip)
    """
    
    def __init__(self, logger_bot: LoggerBot):
        self.strategy = DualSuperTrendStrategy()
        self.position_manager = PositionManager()
        self.logger_bot = logger_bot
    
    async def get_refresh_interval(self, screener_setup: Dict) -> int:
        """
        Get refresh interval in seconds.
        Returns env variable if set, else screener's timeframe.
        """
        default_interval = getattr(settings, 'SCREENER_DEFAULT_REFRESH_INTERVAL', 0)
        
        if default_interval > 0:
            return default_interval * 60  # Convert minutes to seconds
        
        # Use screener's timeframe
        timeframe = screener_setup.get("timeframe", "15m")
        from utils.timeframe import get_timeframe_seconds
        return get_timeframe_seconds(timeframe)
    
    async def fetch_screener_assets(
        self,
        client: DeltaExchangeClient,
        screener_setup: Dict
    ) -> List[str]:
        """Fetch assets based on screener mode."""
        mode = screener_setup.get("screener_mode", "top_gainers")
        top_n = screener_setup.get("top_n", 10)
        timeframe = screener_setup.get("timeframe", "15m")
        
        try:
            if mode == "top_gainers":
                return await get_top_gainers(client, timeframe, top_n)
            
            elif mode == "top_losers":
                return await get_top_losers(client, timeframe, top_n)
            
            elif mode == "both":
                gainers = await get_top_gainers(client, timeframe, top_n)
                losers = await get_top_losers(client, timeframe, top_n)
                return gainers + losers
            
            elif mode == "all_assets":
                return await get_all_perpetual_symbols(client)
            
            else:
                logger.error(f"Unknown screener mode: {mode}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error fetching screener assets: {e}")
            return []
    
    async def filter_assets(
        self,
        screener_assets: List[str],
        screener_setup_id: str
    ) -> List[str]:
        """
        Filter assets based on:
        1. Not in any algo setup (regardless of timeframe)
        2. Not in any other screener position (first-come-first-served)
        """
        allowed_assets = []
        
        # Get all algo assets
        algo_setups = await get_all_active_algo_setups()
        algo_assets = {setup.get("asset") for setup in algo_setups}
        
        for asset in screener_assets:
            # Filter algo assets
            if asset in algo_assets:
                logger.warning(f"⏭️ SKIP {asset}: Already in algo setup")
                continue
            
            # Filter assets with existing screener positions (first-come-first-served)
            existing_positions = await get_screener_positions_by_asset(asset)
            if existing_positions:
                logger.warning(
                    f"⏭️ SKIP {asset}: Already in screener position "
                    f"({existing_positions[0].get('screener_setup_name')})"
                )
                continue
            
            allowed_assets.append(asset)
            logger.info(f"✅ ALLOWED: {asset}")
        
        return allowed_assets
    
    async def check_new_asset_entry(
        self,
        asset: str,
        screener_setup: Dict,
        indicator_result: Dict,
        client: DeltaExchangeClient
    ) -> Optional[Dict]:
        """
        Option 1: Conservative entry for new assets.
        - First appearance: Cache signal, no entry
        - Second appearance: Enter if flipped
        """
        setup_id = str(screener_setup["_id"])
        mode = screener_setup.get("screener_mode")
        
        # Get cached Perusu signal
        cached_perusu = await get_screener_indicator_cache(
            setup_id, asset, "perusu"
        )
        
        current_perusu_signal = indicator_result["perusu"]["signal"]
        
        if not cached_perusu:
            # First time seeing this asset - cache and wait
            logger.info(
                f"🆕 New asset {asset} - Caching Perusu signal "
                f"({'Uptrend' if current_perusu_signal == 1 else 'Downtrend'}), "
                f"waiting for flip..."
            )
            return None
        
        # Second+ appearance - check for flip
        last_signal = cached_perusu.get("last_signal")
        
        if current_perusu_signal != last_signal:
            # Flip detected!
            entry_side = "long" if current_perusu_signal == 1 else "short"
            logger.info(
                f"🔄 Perusu FLIP detected for {asset}: "
                f"{'Downtrend → Uptrend' if entry_side == 'long' else 'Uptrend → Downtrend'}"
            )
            
            return self.strategy.generate_entry_signal(
                setup_id,
                last_signal,
                indicator_result
            )
        
        # No flip yet
        return None
    
    async def process_screener_asset(
        self,
        asset: str,
        screener_setup: Dict,
        client: DeltaExchangeClient
    ):
        """Process single screener asset for entry signals."""
        try:
            setup_id = str(screener_setup["_id"])
            setup_name = screener_setup.get("setup_name")
            timeframe = screener_setup.get("timeframe")
            
            # Calculate indicators
            indicator_result = await self.strategy.calculate_indicators(
                client, asset, timeframe
            )
            
            if not indicator_result:
                logger.warning(f"⚠️ Failed to calculate indicators for {asset}")
                return
            
            # Cache indicators
            await upsert_screener_indicator_cache({
                "screener_setup_id": setup_id,
                "asset": asset,
                "indicator_name": "perusu",
                "timeframe": timeframe,
                "last_signal": indicator_result["perusu"]["signal"],
                "last_value": indicator_result["perusu"]["supertrend_value"],
                "calculated_at": datetime.utcnow()
            })
            
            await upsert_screener_indicator_cache({
                "screener_setup_id": setup_id,
                "asset": asset,
                "indicator_name": "sirusu",
                "timeframe": timeframe,
                "last_signal": indicator_result["sirusu"]["signal"],
                "last_value": indicator_result["sirusu"]["supertrend_value"],
                "calculated_at": datetime.utcnow()
            })
            
            # Check for entry signal (Option 1: wait for flip)
            entry_signal = await self.check_new_asset_entry(
                asset, screener_setup, indicator_result, client
            )
            
            if entry_signal:
                logger.info(f"🚀 Entry signal for {asset}: {entry_signal['side'].upper()}")
                
                # Place entry order
                success = await self.position_manager.place_breakout_entry_order(
                    client=client,
                    algo_setup=screener_setup,  # Pass screener setup as if it's algo
                    entry_side=entry_signal['side'],
                    breakout_price=entry_signal.get('trigger_price'),
                    sirusu_value=indicator_result["sirusu"]["supertrend_value"],
                    immediate=entry_signal.get('immediate', False)
                )
                
                if success:
                    await self.logger_bot.send_trade_entry(
                        setup_name=f"[SCREENER] {setup_name}",
                        asset=asset,
                        direction=entry_signal['side'],
                        entry_price=indicator_result["perusu"]["latest_close"],
                        lot_size=screener_setup.get("lot_size", 1),
                        perusu_signal=indicator_result["perusu"]["signal_text"],
                        sirusu_sl=indicator_result["sirusu"]["supertrend_value"]
                    )
                    
        except Exception as e:
            logger.error(f"❌ Error processing screener asset {asset}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def process_screener_setup(self, screener_setup: Dict):
        """Main screener processing function."""
        setup_id = str(screener_setup["_id"])
        setup_name = screener_setup.get("setup_name", "Unknown")
        
        logger.info(f"📊 Processing Screener: {setup_name}")
        
        try:
            # Get API credentials
            api_id = screener_setup["api_id"]
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred:
                logger.error(f"❌ Failed to load credentials for {setup_name}")
                return
            
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
            
            # Fetch screener assets
            screener_assets = await self.fetch_screener_assets(client, screener_setup)
            logger.info(f"   Total assets found: {len(screener_assets)}")
            
            if not screener_assets:
                logger.warning(f"⚠️ No assets found for {setup_name}")
                await client.close()
                return
            
            # Filter assets
            allowed_assets = await self.filter_assets(screener_assets, setup_id)
            logger.info(f"   Allowed assets: {len(allowed_assets)}")
            
            if not allowed_assets:
                logger.warning(f"⚠️ All assets filtered out for {setup_name}")
                await client.close()
                return
            
            # Process each allowed asset
            for asset in allowed_assets:
                await self.process_screener_asset(asset, screener_setup, client)
            
            await client.close()
            
        except Exception as e:
            logger.error(f"❌ Error processing screener {setup_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def run_continuous_monitoring(self):
        """Main screener monitoring loop."""
        logger.info("🚀 Starting screener monitoring...")
        await self.logger_bot.send_info("🚀 Screener Engine Started")
        
        while True:
            try:
                screener_setups = await get_all_active_screener_setups()
                
                if not screener_setups:
                    logger.debug("ℹ️ No active screener setups")
                    await asyncio.sleep(60)
                    continue
                
                for setup in screener_setups:
                    await self.process_screener_setup(setup)
                
                # Sleep until next refresh (use shortest interval among all setups)
                intervals = [
                    await self.get_refresh_interval(s) for s in screener_setups
                ]
                shortest_interval = min(intervals) if intervals else 900  # Default 15m
                
                logger.info(f"💤 Screener sleeping {shortest_interval}s until next refresh")
                await asyncio.sleep(shortest_interval)
                
            except Exception as e:
                logger.error(f"❌ Exception in screener monitoring: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await asyncio.sleep(60)
