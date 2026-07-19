import json
import logging
from typing import Dict, Optional, Tuple, Any
from telegram.ext import BasePersistence, PersistenceInput
from telegram.ext._utils.types import CDCData, ConversationDict, ConversationKey

from database.mongodb import MongoDB

logger = logging.getLogger(__name__)

class MongoPersistence(BasePersistence):
    """
    MongoDB based persistence for python-telegram-bot v20+.
    Stores user_data, chat_data, bot_data, and conversation states.
    """
    
    def __init__(self, store_data: Optional[PersistenceInput] = None, update_interval: float = 60):
        super().__init__(store_data=store_data, update_interval=update_interval)
        
    def get_db(self):
        return MongoDB.get_db()
        
    # --- CONVERSATIONS ---
    async def get_conversations(self, name: str) -> ConversationDict:
        db = self.get_db()
        doc = await db.bot_persistence.find_one({"_id": f"conv_{name}"})
        if not doc or "data" not in doc:
            return {}
            
        result = {}
        for k, v in doc["data"].items():
            try:
                key_tuple = tuple(json.loads(k))
                result[key_tuple] = v
            except Exception as e:
                logger.warning(f"Failed to parse conversation key {k}: {e}")
        return result

    async def update_conversation(self, name: str, key: ConversationKey, new_state: Optional[object]) -> None:
        db = self.get_db()
        key_str = json.dumps(list(key))
        
        if new_state is None:
            await db.bot_persistence.update_one(
                {"_id": f"conv_{name}"},
                {"$unset": {f"data.{key_str}": ""}},
                upsert=True
            )
        else:
            await db.bot_persistence.update_one(
                {"_id": f"conv_{name}"},
                {"$set": {f"data.{key_str}": new_state}},
                upsert=True
            )

    # --- USER DATA ---
    async def get_user_data(self) -> Dict[int, Any]:
        db = self.get_db()
        doc = await db.bot_persistence.find_one({"_id": "user_data"})
        if not doc or "data" not in doc:
            return {}
        return {int(k): v for k, v in doc["data"].items()}

    async def update_user_data(self, user_id: int, data: Any) -> None:
        if not self.store_data.user_data:
            return
        db = self.get_db()
        if not data:
            await db.bot_persistence.update_one(
                {"_id": "user_data"},
                {"$unset": {f"data.{user_id}": ""}},
                upsert=True
            )
        else:
            await db.bot_persistence.update_one(
                {"_id": "user_data"},
                {"$set": {f"data.{user_id}": data}},
                upsert=True
            )

    async def refresh_user_data(self, user_id: int, user_data: Any) -> None:
        pass
        
    async def drop_user_data(self, user_id: int) -> None:
        db = self.get_db()
        await db.bot_persistence.update_one(
            {"_id": "user_data"},
            {"$unset": {f"data.{user_id}": ""}}
        )

    # --- CHAT DATA ---
    async def get_chat_data(self) -> Dict[int, Any]:
        db = self.get_db()
        doc = await db.bot_persistence.find_one({"_id": "chat_data"})
        if not doc or "data" not in doc:
            return {}
        return {int(k): v for k, v in doc["data"].items()}

    async def update_chat_data(self, chat_id: int, data: Any) -> None:
        if not self.store_data.chat_data:
            return
        db = self.get_db()
        if not data:
            await db.bot_persistence.update_one(
                {"_id": "chat_data"},
                {"$unset": {f"data.{chat_id}": ""}},
                upsert=True
            )
        else:
            await db.bot_persistence.update_one(
                {"_id": "chat_data"},
                {"$set": {f"data.{chat_id}": data}},
                upsert=True
            )

    async def refresh_chat_data(self, chat_id: int, chat_data: Any) -> None:
        pass

    async def drop_chat_data(self, chat_id: int) -> None:
        db = self.get_db()
        await db.bot_persistence.update_one(
            {"_id": "chat_data"},
            {"$unset": {f"data.{chat_id}": ""}}
        )

    # --- BOT DATA ---
    async def get_bot_data(self) -> Any:
        db = self.get_db()
        doc = await db.bot_persistence.find_one({"_id": "bot_data"})
        if not doc or "data" not in doc:
            return {}
        return doc["data"]

    async def update_bot_data(self, data: Any) -> None:
        if not self.store_data.bot_data:
            return
        db = self.get_db()
        await db.bot_persistence.update_one(
            {"_id": "bot_data"},
            {"$set": {"data": data}},
            upsert=True
        )

    async def refresh_bot_data(self, bot_data: Any) -> None:
        pass

    # --- CALLBACK DATA ---
    async def get_callback_data(self) -> Optional[CDCData]:
        return None

    async def update_callback_data(self, data: CDCData) -> None:
        pass

    async def flush(self) -> None:
        pass
