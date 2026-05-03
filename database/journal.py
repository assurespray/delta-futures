"""MongoDB operations for the Trade Journal and P&L Tracking System."""
import logging
from typing import Dict, Any, List, Optional
from database.mongodb import mongodb

logger = logging.getLogger(__name__)

class JournalOperations:
    def __init__(self):
        self._collection_name = "journal_trades"

    @property
    def collection(self):
        return mongodb.get_db()[self._collection_name]

    async def log_trade_event(self, trade_id: str, event_data: Dict[str, Any]) -> bool:
        """Upserts an exact ledger entry for a trade event."""
        try:
            await self.collection.update_one(
                {"trade_id": trade_id},
                {"$set": event_data},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log trade event to journal: {e}")
            return False

    async def append_scaling_event(self, trade_id: str, scaling_event: Dict[str, Any]) -> bool:
        """Appends a scaling event (partial exit, re-entry) to the trade."""
        try:
            await self.collection.update_one(
                {"trade_id": trade_id},
                {"$push": {"scaling_events": scaling_event}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to append scaling event: {e}")
            return False

    async def get_trades_by_asset(self, user_id: str, asset: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch closed trades for a user, optionally filtered by asset."""
        try:
            query = {"user_id": user_id, "status": "closed"}
            if asset and asset != "ALL":
                query["asset"] = asset
            cursor = self.collection.find(query).sort("exit_time", -1)
            return await cursor.to_list(1000)
        except Exception as e:
            logger.error(f"Failed to get journal trades: {e}")
            return []

    async def get_recent_trades(self, user_id: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch the most recent closed trades for the user."""
        try:
            query = {"user_id": user_id, "status": "closed"}
            cursor = self.collection.find(query).sort("exit_time", -1).limit(limit)
            return await cursor.to_list(limit)
        except Exception as e:
            logger.error(f"Failed to get recent trades: {e}")
            return []

    async def get_traded_assets(self, user_id: str) -> List[str]:
        """Get distinct list of assets traded by the user."""
        try:
            return await self.collection.distinct("asset", {"user_id": user_id})
        except Exception as e:
            logger.error(f"Failed to get traded assets: {e}")
            return []

journal_ops = JournalOperations()
