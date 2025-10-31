"""Screener processing engine with asset-level filtering."""
import logging
from typing import List, Dict
from utils.duplicate_filter import DuplicateFilter
from database.crud import get_all_active_algo_setups

logger = logging.getLogger(__name__)


class ScreenerEngine:
    """
    Process screener setups with intelligent asset filtering.
    
    ✅ RULE: If asset+timeframe in Algo → Skip it (Algo has priority)
    """
    
    def __init__(self):
        """Initialize screener engine."""
        self.duplicate_filter = DuplicateFilter()
    
    async def process_screener_setup(
        self,
        screener_setup: Dict,
        screener_assets: List[str]
    ):
        """
        Process screener setup with asset filtering.
        
        ✅ For each asset:
        • If in Algo with same timeframe → ❌ SKIP (Algo has priority)
        • Otherwise → ✅ TRADE
        
        Args:
            screener_setup: Screener configuration
            screener_assets: Assets from screener (e.g., [BTCUSD, ETHUSD, ADAUSD])
        """
        
        setup_name = screener_setup.get("setup_name", "Unknown")
        screener_tf = screener_setup.get("timeframe", "")
        
        logger.info(f"📊 Processing Screener: {setup_name}")
        logger.info(f"   Total assets found: {len(screener_assets)}")
        
        try:
            # Get all active algo setups
            algo_setups = await get_all_active_algo_setups()
            
            # ✅ Filter each asset individually
            allowed_assets = []
            blocked_assets = []
            
            for asset in screener_assets:
                # Check if this asset+timeframe exists in any algo
                is_duplicate = await self.duplicate_filter.check_duplicate_for_screener_asset(
                    screener_asset=asset,
                    screener_timeframe=screener_tf,
                    algo_setups=algo_setups
                )
                
                if is_duplicate:
                    blocked_assets.append(asset)
                    logger.warning(f"   ⏭️ SKIP: {asset} (Algo has priority)")
                else:
                    allowed_assets.append(asset)
                    logger.info(f"   ✅ TRADE: {asset}")
            
            # Log summary
            logger.info(
                f"\n📋 Screener '{setup_name}' Filtering Summary:\n"
                f"   ✅ Will trade: {len(allowed_assets)} assets\n"
                f"   ⏭️ Skipped (Algo priority): {len(blocked_assets)} assets"
            )
            
            if blocked_assets:
                logger.info(f"   Skipped assets: {', '.join(blocked_assets)}")
            
            # ✅ NOW TRADE ONLY ALLOWED ASSETS
            if allowed_assets:
                logger.info(f"\n🚀 Trading {len(allowed_assets)} assets from screener:")
                for asset in allowed_assets:
                    logger.info(f"   • {asset} @ {screener_tf}")
                    # TODO: Execute trade for this asset
                    # await self.position_manager.process_asset(asset, screener_setup)
            else:
                logger.warning(f"⚠️ No assets to trade (all {len(blocked_assets)} blocked by algos)")
        
        except Exception as e:
            logger.error(f"❌ Error processing screener: {e}")
            
