"""Duplicate asset filtering between Algo and Screener setups."""
import logging
from typing import Dict, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class DuplicateFilter:
    """Filter duplicate assets between Algo and Screener setups."""
    
    def __init__(self):
        """Initialize filter."""
        self.logger = logger
    
    async def get_screener_assets(self, screener_setups: List[Dict]) -> Dict[str, Set[str]]:
        """
        Extract assets from screener setups.
        
        Args:
            screener_setups: List of active screener setups
        
        Returns:
            Dict mapping screener_id -> set of asset symbols
        """
        screener_assets = {}
        
        for setup in screener_setups:
            setup_id = str(setup.get("_id", ""))
            asset_type = setup.get("asset_selection_type", "")
            
            # For now, we'll store the selection type
            # In production, you'd fetch actual assets here
            screener_assets[setup_id] = {
                "type": asset_type,
                "setup_name": setup.get("setup_name", "Unknown")
            }
        
        return screener_assets
    
    def get_algo_assets(self, algo_setups: List[Dict]) -> Dict[str, str]:
        """
        Extract assets from algo setups (single asset each).
        
        Args:
            algo_setups: List of active algo setups
        
        Returns:
            Dict mapping setup_id -> asset symbol
        """
        algo_assets = {}
        
        for setup in algo_setups:
            setup_id = str(setup.get("_id", ""))
            asset = setup.get("asset", "").upper()
            
            if asset:
                algo_assets[setup_id] = asset
        
        return algo_assets
    
    async def check_duplicate(
        self,
        algo_asset: str,
        screener_setups: List[Dict]
    ) -> Optional[Dict[str, str]]:
        """
        Check if an algo asset is in ANY active screener.
        
        Args:
            algo_asset: Asset symbol from algo setup
            screener_setups: List of active screener setups
        
        Returns:
            Dict with screener_id and screener_name if duplicate, None otherwise
        """
        algo_asset_upper = algo_asset.upper()
        
        for setup in screener_setups:
            setup_id = str(setup.get("_id", ""))
            setup_name = setup.get("setup_name", "Unknown")
            asset_type = setup.get("asset_selection_type", "")
            
            # Check if this screener type would include the algo asset
            # This is a simplified check - in production you'd verify against actual screener assets
            is_duplicate = await self._is_asset_in_screener_type(
                algo_asset_upper,
                asset_type
            )
            
            if is_duplicate:
                return {
                    "screener_id": setup_id,
                    "screener_name": setup_name,
                    "asset_type": asset_type
                }
        
        return None
    
    async def _is_asset_in_screener_type(self, asset: str, screener_type: str) -> bool:
        """
        Check if asset would be included in screener type.
        
        For MVP: only "every" type includes all assets.
        For others, we'd need to fetch actual market data.
        
        Args:
            asset: Asset symbol
            screener_type: Screener selection type
        
        Returns:
            True if duplicate, False otherwise
        """
        if screener_type == "every":
            # "Every asset" includes all assets
            return True
        
        # For other types (gainers, losers, mixed), we'd need actual market data
        # For now, return False to be conservative
        # TODO: Implement actual market data fetching for gainers/losers
        
        return False
    
    def format_duplicate_message(self, duplicate_info: Dict[str, str], algo_name: str) -> str:
        """
        Format duplicate warning message.
        
        Args:
            duplicate_info: Dict with screener_id, screener_name, asset_type
            algo_name: Name of algo setup
        
        Returns:
            Formatted message
        """
        message = (
            f"⚠️ **Duplicate Asset Detected**\n\n"
            f"**Algo Setup:** {algo_name}\n"
            f"**Screener Setup:** {duplicate_info['screener_name']}\n"
            f"**Screener Type:** {duplicate_info['asset_type']}\n\n"
            f"This asset is being monitored by both setups!\n"
            f"✅ **Action:** Skipping trade from SCREENER (Algo has priority)"
        )
        return message
      
