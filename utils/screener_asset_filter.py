"""Asset-level filtering for screener setups - Skip duplicates, trade rest."""
import logging
from typing import List, Dict, Set

logger = logging.getLogger(__name__)


class ScreenerAssetFilter:
    """
    Filter assets within a screener setup.
    
    ✅ KEY FEATURE: Skips duplicate assets only, trades all other assets in screener
    """
    
    def __init__(self):
        """Initialize asset filter."""
        self.logger = logger
    
    async def get_filtered_assets_for_screener(
        self,
        screener_setup: Dict,
        algo_setups: List[Dict]
    ) -> Dict[str, List[str]]:
        """
        Get assets from screener, excluding duplicates with algos.
        
        ✅ SMART FILTERING: Removes conflicts, keeps everything else
        
        Args:
            screener_setup: Screener setup config
            algo_setups: List of active algo setups
        
        Returns:
            Dict with:
            - 'assets_to_trade': Assets NOT in algos (safe to trade)
            - 'filtered_out': Assets that were duplicates (skipped)
            - 'asset_count': How many assets will actually trade
        """
        
        screener_id = str(screener_setup.get("_id", ""))
        screener_tf = screener_setup.get("timeframe", "").lower()
        setup_name = screener_setup.get("setup_name", "Unknown")
        
        # Get all assets from this screener type
        screener_assets = await self._get_screener_assets(screener_setup)
        
        if not screener_assets:
            logger.warning(f"⚠️ No assets found for screener: {setup_name}")
            return {
                "assets_to_trade": [],
                "filtered_out": [],
                "asset_count": 0
            }
        
        # Get all algo assets with their timeframes
        algo_assets_with_tf = self._get_algo_assets_with_timeframe(algo_setups)
        
        # Filter: Remove duplicates (same asset + same timeframe)
        assets_to_trade = []
        filtered_out = []
        
        for asset in screener_assets:
            is_duplicate = await self._is_duplicate(asset, screener_tf, algo_assets_with_tf)
            
            if is_duplicate:
                filtered_out.append(asset)
                logger.warning(f"⚠️ Duplicate asset in screener: {asset} @ {screener_tf} - SKIPPING")
            else:
                assets_to_trade.append(asset)
                logger.info(f"✅ Safe asset in screener: {asset} - WILL TRADE")
        
        self.logger.info(
            f"📊 Screener '{setup_name}' filtering complete:\n"
            f"   Total assets: {len(screener_assets)}\n"
            f"   Will trade: {len(assets_to_trade)}\n"
            f"   Filtered out (duplicates): {len(filtered_out)}"
        )
        
        return {
            "screener_id": screener_id,
            "assets_to_trade": assets_to_trade,
            "filtered_out": filtered_out,
            "asset_count": len(assets_to_trade),
            "filter_count": len(filtered_out)
        }
    
    async def _get_screener_assets(self, screener_setup: Dict) -> List[str]:
        """
        Get actual assets for screener based on type.
        
        Args:
            screener_setup: Screener config
        
        Returns:
            List of asset symbols
        """
        asset_type = screener_setup.get("asset_selection_type", "")
        
        if asset_type == "every":
            # Placeholder - in production, fetch all assets
            return ["BTCUSD", "ETHUSD", "ADAUSD", "SOLAUSD", "XRPUSD"]
        
        elif asset_type == "gainers":
            # Placeholder - in production, fetch top gainers
            return ["ETHUSD", "SOLAUSD", "AVAXUSD"]
        
        elif asset_type == "losers":
            # Placeholder - in production, fetch top losers
            return ["BTCUSD", "DOGEUSD"]
        
        elif asset_type == "mixed":
            # Placeholder - gainers + losers
            return ["ETHUSD", "SOLAUSD", "BTCUSD", "DOGEUSD"]
        
        return []
    
    def _get_algo_assets_with_timeframe(self, algo_setups: List[Dict]) -> Dict[str, str]:
        """
        Get all algo assets with their timeframes.
        
        Args:
            algo_setups: List of active algo setups
        
        Returns:
            Dict mapping asset -> timeframe
            Example: {"BTCUSD": "5m", "ETHUSD": "1h"}
        """
        algo_assets = {}
        
        for setup in algo_setups:
            asset = setup.get("asset", "").upper()
            tf = setup.get("timeframe", "").lower()
            
            if asset and tf:
                algo_assets[asset] = tf
        
        return algo_assets
    
    async def _is_duplicate(
        self,
        asset: str,
        screener_tf: str,
        algo_assets_with_tf: Dict[str, str]
    ) -> bool:
        """
        Check if asset+timeframe is duplicate with any algo.
        
        Args:
            asset: Asset symbol
            screener_tf: Screener timeframe
            algo_assets_with_tf: Dict of algo assets and timeframes
        
        Returns:
            True if duplicate, False otherwise
        """
        asset_upper = asset.upper()
        
        # Check if this asset exists in algos
        if asset_upper in algo_assets_with_tf:
            algo_tf = algo_assets_with_tf[asset_upper]
            
            # Check if timeframes match
            if algo_tf == screener_tf:
                return True
        
        return False
    
    def format_filter_report(self, filter_result: Dict) -> str:
        """
        Format filtering report for logging.
        
        Args:
            filter_result: Result from get_filtered_assets_for_screener
        
        Returns:
            Formatted message
        """
        if filter_result["filter_count"] == 0:
            message = f"✅ All assets are safe! Trading {filter_result['asset_count']} assets"
        else:
            filtered = ", ".join(filter_result["filtered_out"])
            message = (
                f"📊 Screener filtering complete\n"
                f"✅ Will trade: {filter_result['asset_count']} assets\n"
                f"⚠️ Skipped (duplicates): {filtered}"
            )
        
        return message
      
