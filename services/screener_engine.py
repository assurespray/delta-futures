"""Screener processing engine with asset-level filtering."""
import logging
from typing import List, Dict
from utils.screener_asset_filter import ScreenerAssetFilter
from utils.duplicate_filter import DuplicateFilter
from database.crud import get_all_active_algo_setups

logger = logging.getLogger(__name__)


class ScreenerEngine:
    """Process screener setups with intelligent asset filtering."""
    
    def __init__(self):
        """Initialize screener engine."""
        self.asset_filter = ScreenerAssetFilter()
        self.duplicate_filter = DuplicateFilter()
    
    async def process_screener_setup(
        self,
        screener_setup: Dict,
        screener_assets: List[str]
    ):
        """
        Process screener setup with asset filtering.
        
        ✅ Two levels of filtering:
        1. Asset-level: Skip only duplicates
        2. Keep all other assets trading
        
        Args:
            screener_setup: Screener configuration
            screener_assets: Assets from screener (e.g., [BTCUSD, ETHUSD, ADAUSD])
        """
        
        setup_name = screener_setup.get("setup_name", "Unknown")
        screener_tf = screener_setup.get("timeframe", "")
        
        logger.info(f"📊 Processing Screener: {setup_name}")
        
        try:
            # Get all active algo setups
            algo_setups = await get_all_active_algo_setups()
            
            # ✅ Filter each asset against algos
            allowed_assets = []
            blocked_assets = []
            
            for asset in screener_assets:
                is_duplicate = await self.duplicate_filter.check_duplicate_for_screener_asset(
                    screener_asset=asset,
                    screener_timeframe=screener_tf,
                    algo_setups=algo_setups
                )
                
                if is_duplicate:
                    blocked_assets.append(asset)
                    logger.warning(f"⏭️ SKIPPING: {asset} (duplicate with algo)")
                else:
                    allowed_assets.append(asset)
                    logger.info(f"✅ ALLOWING: {asset} (safe to trade)")
            
            # Log summary
            if blocked_assets:
                logger.info(
                    f"📊 Filtering complete:\n"
                    f"   Will trade: {len(allowed_assets)}\n"
                    f"   Skipped: {', '.join(blocked_assets)}"
                )
            else:
                logger.info(f"✅ All {len(allowed_assets)} assets cleared for trading!")
            
            # Now process allowed assets
            if allowed_assets:
                logger.info(f"🚀 Trading {len(allowed_assets)} assets from screener")
                for asset in allowed_assets:
                    logger.info(f"   • {asset}")
                    # TODO: Execute trade logic for this asset
                    # await self.position_manager.process_asset(asset, screener_setup)
            else:
                logger.warning(f"⚠️ No assets left to trade after filtering!")
        
        except Exception as e:
            logger.error(f"❌ Error processing screener: {e}")
            
