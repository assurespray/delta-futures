"""Duplicate asset filtering between Algo and Screener setups."""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DuplicateFilter:
    """Filter duplicate assets between Algo and Screener setups.
    
    ✅ NOW CHECKS: Asset + Timeframe combination
    ✅ TWO-LEVEL: Algo level (skip entire setup) + Screener level (skip asset only)
    """
    
    def __init__(self):
        """Initialize filter."""
        self.logger = logger
    
    async def check_duplicate_for_algo(
        self,
        algo_asset: str,
        algo_timeframe: str,
        screener_setups: List[Dict]
    ) -> Optional[Dict[str, str]]:
        """
        ALGO LEVEL: Check if algo should be skipped entirely.
        
        ✅ Only skips if:
        • Same asset + same timeframe found in screener
        • AND screener type that would include this asset
        
        Args:
            algo_asset: Asset from algo
            algo_timeframe: Timeframe from algo
            screener_setups: All screener setups
        
        Returns:
            Dict if duplicate, None otherwise
        """
        
        algo_asset_upper = algo_asset.upper()
        algo_tf_lower = algo_timeframe.lower()
        
        for screener in screener_setups:
            screener_tf = screener.get("timeframe", "").lower()
            screener_type = screener.get("asset_selection_type", "")
            
            # ✅ Step 1: Check timeframe match
            if algo_tf_lower != screener_tf:
                continue  # Different timeframe, not a problem
            
            # ✅ Step 2: Check if asset would be in this screener type
            would_include = self._screener_type_would_include(screener_type)
            
            if would_include or screener_type == "every":
                # ✅ DUPLICATE FOUND
                return {
                    "screener_id": str(screener.get("_id", "")),
                    "screener_name": screener.get("setup_name", "Unknown"),
                    "screener_type": screener_type,
                    "screener_timeframe": screener_tf
                }
        
        return None
    
    async def check_duplicate_for_screener_asset(
        self,
        screener_asset: str,
        screener_timeframe: str,
        algo_setups: List[Dict]
    ) -> bool:
        """
        SCREENER LEVEL: Check if a specific asset should be skipped.
        
        ✅ Only returns True if:
        • Same asset exists in algo
        • AND same timeframe
        
        Args:
            screener_asset: Asset from screener
            screener_timeframe: Timeframe from screener
            algo_setups: All algo setups
        
        Returns:
            True if duplicate (skip), False otherwise
        """
        
        screener_asset_upper = screener_asset.upper()
        screener_tf = screener_timeframe.lower()
        
        for algo in algo_setups:
            algo_asset = algo.get("asset", "").upper()
            algo_tf = algo.get("timeframe", "").lower()
            
            # ✅ Check both asset AND timeframe
            if algo_asset == screener_asset_upper and algo_tf == screener_tf:
                self.logger.warning(
                    f"⚠️ DUPLICATE: {screener_asset_upper} @ {screener_tf} "
                    f"(found in algo: {algo.get('setup_name', 'Unknown')})"
                )
                return True
        
        return False
    
    def _screener_type_would_include(self, screener_type: str) -> bool:
        """
        Determine if screener type would trade all assets (not just specific list).
        
        ✅ "every" → Yes, all assets
        ✅ "gainers" → No, only top gainers (unknown list)
        ✅ "losers" → No, only top losers (unknown list)
        ✅ "mixed" → No, only gainers+losers (unknown list)
        """
        return screener_type == "every"
    
    def format_duplicate_message(self, duplicate_info: Dict[str, str], algo_name: str) -> str:
        """Format duplicate warning message."""
        return (
            f"⚠️ **Duplicate Detected**\n\n"
            f"**Algo:** {algo_name}\n"
            f"**Screener:** {duplicate_info['screener_name']}\n"
            f"**Issue:** Same asset+timeframe\n\n"
            f"✅ **Action:** Algo skipped (Screener has priority)"
        )
        
