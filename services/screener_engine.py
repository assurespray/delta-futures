"""Screener processing engine with asset-level filtering."""
import logging
from typing import List, Dict
from utils.screener_asset_filter import ScreenerAssetFilter
from database.crud import get_all_active_algo_setups

logger = logging.getLogger(__name__)


class ScreenerEngine:
    """Process screener setups with intelligent asset filtering."""
    
    def __init__(self):
        """Initialize screener engine."""
        self.asset_filter = ScreenerAssetFilter()
    
    async def process_screener_setup(self, screener_setup: Dict):
        """
        Process screener setup with asset filtering.
        
        ✅ NEW: Filters duplicate assets, trades the rest
        
        Args:
            screener_setup: Screener configuration
        """
        
        screener_id = str(screener_setup.get("_id", ""))
        setup_name = screener_setup.get("setup_name", "Unknown")
        
        logger.info(f"📊 Processing screener: {setup_name}")
        
        try:
            # Get all active algo setups
            algo_setups = await get_all_active_algo_setups()
            
            # Get filtered assets (removes duplicates)
            filter_result = await self.asset_filter.get_filtered_assets_for_screener(
                screener_setup,
                algo_setups
            )
            
            assets_to_trade = filter_result["assets_to_trade"]
            
            # If no assets left after filtering, skip
            if not assets_to_trade:
                logger.warning(
                    f"⚠️ Screener '{setup_name}' has NO assets to trade "
                    f"(all {filter_result['filter_count']} are duplicates)"
                )
                return
            
            # Log filtering report
            report = self.asset_filter.format_filter_report(filter_result)
            logger.info(f"📋 Filter Report:\n{report}")
            
            # Now trade only the filtered assets
            logger.info(f"🚀 Trading {len(assets_to_trade)} assets from screener")
            
            for asset in assets_to_trade:
                logger.info(f"   • {asset}")
                # TODO: Execute trade logic for this asset
                # await self.position_manager.process_asset(asset, screener_setup)
        
        except Exception as e:
            logger.error(f"❌ Error processing screener: {e}")
          
