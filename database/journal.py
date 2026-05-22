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

    async def get_trade_by_id(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single journal entry by trade_id."""
        try:
            return await self.collection.find_one({"trade_id": trade_id})
        except Exception as e:
            logger.error(f"Failed to get journal trade by id: {e}")
            return None

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

    async def get_trades_by_asset(self, user_id: str, asset: Optional[str] = None, is_paper_trade: bool = False, strategy: Optional[str] = None, api_name: Optional[str] = None, direction: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch closed trades for a user, optionally filtered by asset, strategy, api_name, direction, and trade type."""
        try:
            query = {"user_id": user_id, "status": "closed", "is_paper_trade": is_paper_trade}
            if asset and asset != "ALL":
                query["asset"] = asset
            if strategy and strategy != "ALL":
                query["strategy_name"] = strategy
            if api_name:
                if api_name == "DeltaExchange":
                    query["api_name"] = {"$in": ["DeltaExchange", None, ""]}
                else:
                    query["api_name"] = api_name
            if direction and direction != "all":
                query["direction"] = direction
            cursor = self.collection.find(query).sort("exit_time", -1)
            return await cursor.to_list(1000)
        except Exception as e:
            logger.error(f"Failed to get journal trades: {e}")
            return []

    async def get_recent_trades(self, user_id: str, limit: int = 15, is_paper_trade: bool = False, strategy: Optional[str] = None, asset: Optional[str] = None, api_name: Optional[str] = None, direction: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch the most recent closed trades for the user."""
        try:
            query = {"user_id": user_id, "status": "closed", "is_paper_trade": is_paper_trade}
            if strategy and strategy != "ALL":
                query["strategy_name"] = strategy
            if asset and asset != "ALL":
                query["asset"] = asset
            if api_name:
                if api_name == "DeltaExchange":
                    query["api_name"] = {"$in": ["DeltaExchange", None, ""]}
                else:
                    query["api_name"] = api_name
            if direction and direction != "all":
                query["direction"] = direction
            cursor = self.collection.find(query).sort("exit_time", -1).limit(limit)
            return await cursor.to_list(limit)
        except Exception as e:
            logger.error(f"Failed to get recent trades: {e}")
            return []

    async def get_traded_api_names(self, user_id: str, is_paper_trade: bool = False, direction: Optional[str] = None) -> List[str]:
        """Get unique list of API names used in closed trades by a user."""
        try:
            query = {
                "user_id": user_id,
                "is_paper_trade": is_paper_trade,
                "status": "closed"
            }
            if direction and direction != "all":
                query["direction"] = direction
            raw = await self.collection.distinct("api_name", query)
            # Normalize: None and "" — try to resolve from user's actual API credentials
            names = set()
            for name in raw:
                if not name:
                    names.add("Unknown API")
                else:
                    names.add(name)
            return sorted(names)
        except Exception as e:
            logger.error(f"Failed to fetch traded API names: {e}")
            return []

    async def get_traded_strategies(self, user_id: str, is_paper_trade: bool = False, api_name: Optional[str] = None, direction: Optional[str] = None) -> List[str]:
        """Get unique list of strategies traded by a user, optionally filtered by API name and direction."""
        try:
            query = {
                "user_id": user_id,
                "is_paper_trade": is_paper_trade,
                "status": "closed"
            }
            if api_name:
                if api_name == "DeltaExchange":
                    query["api_name"] = {"$in": ["DeltaExchange", None, ""]}
                else:
                    query["api_name"] = api_name
            if direction and direction != "all":
                query["direction"] = direction
            return await self.collection.distinct("strategy_name", query)
        except Exception as e:
            logger.error(f"Failed to fetch traded strategies: {e}")
            return []

    async def get_traded_assets_by_strategy(self, user_id: str, strategy: str, is_paper_trade: bool = False, api_name: Optional[str] = None, direction: Optional[str] = None) -> List[str]:
        """Get unique list of assets traded by a user under a specific strategy, optionally filtered by API name and direction."""
        try:
            query = {
                "user_id": user_id,
                "strategy_name": strategy,
                "is_paper_trade": is_paper_trade,
                "status": "closed"
            }
            if api_name:
                if api_name == "DeltaExchange":
                    query["api_name"] = {"$in": ["DeltaExchange", None, ""]}
                else:
                    query["api_name"] = api_name
            if direction and direction != "all":
                query["direction"] = direction
            return await self.collection.distinct("asset", query)
        except Exception as e:
            logger.error(f"Failed to fetch traded assets for strategy {strategy}: {e}")
            return []

    async def get_traded_assets(self, user_id: str, is_paper_trade: bool = False) -> List[str]:
        """Get unique list of assets traded by a user."""
        try:
            return await self.collection.distinct("asset", {
                "user_id": user_id,
                "is_paper_trade": is_paper_trade
            })
        except Exception as e:
            logger.error(f"Failed to fetch traded assets: {e}")
            return []

    async def clear_paper_journal(self, user_id: str) -> bool:
        """Deletes all paper trades from the journal for a specific user."""
        try:
            await self.collection.delete_many({
                "user_id": user_id,
                "is_paper_trade": True
            })
            return True
        except Exception as e:
            logger.error(f"Failed to clear paper journal: {e}")
            return False

journal_ops = JournalOperations()
