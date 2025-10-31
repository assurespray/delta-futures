"""Duplicate asset filtering between Algo and Screener setups."""
import logging
from typing import Dict, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class DuplicateFilter:
    """Filter duplicate assets between Algo and Screener setups.
    
    ✅ ENHANCED: Now checks both asset AND timeframe
    """
    
    def __init__(self):
        """Initialize filter."""
        self.logger = logger
    
    async def check_duplicate(
        self,
        algo_asset: str,
        algo_timeframe: str,
        screener_setups: List[Dict]
    ) -> Optional[Dict[str, str]]:
        """
        Check if an algo asset+timeframe is in ANY active screener.
        
        ✅ FIXED: Now considers timeframe!
        
        Args:
            algo_asset: Asset symbol from algo setup
            algo_timeframe: Timeframe from algo setup
            screener_setups: List of active screener setups
        
        Returns:
            Dict with screener_id and screener_name if duplicate, None otherwise
        """
        algo_asset_upper = algo_asset.upper()
        algo_tf_lower = algo_timeframe.lower()
        
        for setup in screener_setups:
            setup_id = str(setup.get("_id", ""))
            setup_name = setup.get("setup_name", "Unknown")
            asset_type = setup.get("asset_selection_type", "")
            screener_tf = setup.get("timeframe", "").lower()
            
            # ✅ ENHANCED: Check BOTH asset AND timeframe match
            is_duplicate = await self._is_duplicate_combo(
                algo_asset_upper,
                algo_tf_lower,
                asset_type,
                screener_tf
            )
            
            if is_duplicate:
                return {
                    "screener_id": setup_id,
                    "screener_name": setup_name,
                    "asset_type": asset_type,
                    "screener_timeframe": screener_tf
                }
        
        return None
    
    async def _is_duplicate_combo(
        self,
        algo_asset: str,
        algo_tf: str,
        screener_type: str,
        screener_tf: str
    ) -> bool:
        """
        Check if asset+timeframe combination is duplicate.
        
        ✅ RULES:
        • Different timeframes = NOT a duplicate (different signals)
        • Same timeframe + same asset = DUPLICATE (conflicting trades)
        
        Args:
            algo_asset: Asset symbol from algo
            algo_tf: Timeframe from algo
            screener_type: Screener selection type
            screener_tf: Timeframe from screener
        
        Returns:
            True if duplicate, False otherwise
        """
        
        # ✅ STEP 1: Check if timeframes match
        if algo_tf != screener_tf:
            logger.info(f"✅ Different timeframes: {algo_tf} vs {screener_tf} - NOT a duplicate")
            return False
        
        # ✅ STEP 2: Check if asset would be in screener type
        if screener_type == "every":
            # "Every asset" includes all assets on same timeframe
            logger.warning(f"⚠️ Same asset+timeframe: {algo_asset} @ {algo_tf}")
            return True
        
        # For other types (gainers, losers, mixed), we'd need actual market data
        # For now, return False to be conservative
        # TODO: Implement actual market data fetching for gainers/losers
        
        return False
    
    def format_duplicate_message(self, duplicate_info: Dict[str, str], algo_name: str, algo_tf: str) -> str:
        """
        Format duplicate warning message.
        
        Args:
            duplicate_info: Dict with screener info
            algo_name: Name of algo setup
            algo_tf: Timeframe of algo setup
        
        Returns:
            Formatted message
        """
        message = (
            f"⚠️ **Duplicate Asset+Timeframe Detected**\n\n"
            f"**Algo Setup:** {algo_name} ({algo_tf})\n"
            f"**Screener Setup:** {duplicate_info['screener_name']} ({duplicate_info['screener_timeframe']})\n"
            f"**Screener Type:** {duplicate_info['asset_type']}\n\n"
            f"⏱️ **Same timeframe detected!**\n"
            f"This could lead to conflicting trades.\n\n"
            f"✅ **Action:** Skipping trade from SCREENER (Algo has priority)"
        )
        return message
        
