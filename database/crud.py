"""CRUD operations for database collections with asset lock support."""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
from database.mongodb import mongodb
from database.models import APICredential, AlgoSetup, TradeState, IndicatorCache, PositionLock, PaperBalance
from cryptography.fernet import Fernet
from config.settings import settings
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.models import OrderRecord, StrategyPreset

logger = logging.getLogger(__name__)

# Initialize Fernet cipher for encryption
cipher_suite = Fernet(settings.encryption_key.encode())


# ==================== Helper Functions ====================

async def get_db() -> AsyncIOMotorDatabase:
    """Get database instance safely."""
    return mongodb.get_db()


# ==================== API Credentials CRUD ====================

async def create_api_credential(user_id: str, api_name: str, api_key: str, api_secret: str) -> str:
    """Create new API credential with encryption."""
    try:
        # Encrypt sensitive data
        encrypted_key = cipher_suite.encrypt(api_key.encode()).decode()
        encrypted_secret = cipher_suite.encrypt(api_secret.encode()).decode()
        
        credential = APICredential(
            user_id=user_id,
            api_name=api_name,
            api_key=encrypted_key,
            api_secret=encrypted_secret
        )
        
        result = await mongodb.get_db().api_credentials.insert_one(credential.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ API credential created for user {user_id}: {api_name}")
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"❌ Failed to create API credential: {e}")
        raise


async def get_api_credentials_by_user(user_id: str) -> List[Dict[str, Any]]:
    """Get all API credentials for a user (without decrypting)."""
    try:
        cursor = mongodb.get_db().api_credentials.find({"user_id": user_id})
        credentials = await cursor.to_list(length=100)
        
        # Convert ObjectId to string and remove encrypted values
        for cred in credentials:
            cred["_id"] = str(cred["_id"])
            cred.pop("api_key", None)
            cred.pop("api_secret", None)
        
        return credentials
        
    except Exception as e:
        logger.error(f"❌ Failed to get API credentials: {e}")
        return []


async def get_api_credential_by_id(credential_id: str, decrypt: bool = True) -> Optional[Dict[str, Any]]:
    """Get API credential by ID with optional decryption."""
    try:
        credential = await mongodb.get_db().api_credentials.find_one({"_id": ObjectId(credential_id)})
        
        if credential and decrypt:
            credential["api_key"] = cipher_suite.decrypt(credential["api_key"].encode()).decode()
            credential["api_secret"] = cipher_suite.decrypt(credential["api_secret"].encode()).decode()
        
        if credential:
            credential["_id"] = str(credential["_id"])
        
        return credential
        
    except Exception as e:
        logger.error(f"❌ Failed to get API credential by ID: {e}")
        return None


async def delete_api_credential(credential_id: str, user_id: str) -> bool:
    """
    Delete API credential and cascade delete all related setups and their data using 'api_id'.
    """
    db = await get_db()

    # Find all setups linked to this API credential
    setups = await mongodb.get_db().algo_setups.find({"api_id": credential_id}).to_list(100)

    # For each setup, delete all related records by setup ID
    for setup in setups:
        setup_id = str(setup["_id"])
        await mongodb.get_db().orders.delete_many({"algo_setup_id": setup_id})
        await mongodb.get_db().positions.delete_many({"algo_setup_id": setup_id})
        await mongodb.get_db().trade_states.delete_many({"setup_id": setup_id})
        await mongodb.get_db().position_locks.delete_many({"setup_id": setup_id})
        await mongodb.get_db().indicator_cache.delete_many({"setup_id": setup_id})
        await mongodb.get_db().screener_indicator_cache.delete_many({"screener_setup_id": setup_id})

    # Delete setups linked to this credential
    await mongodb.get_db().algo_setups.delete_many({"api_id": credential_id})

    # Finally, delete the credential itself
    result = await mongodb.get_db().api_credentials.delete_one({
        "_id": ObjectId(credential_id),
        "user_id": user_id
    })

    if result.deleted_count > 0:
        logger.info(f"🗑️ Cascade deleted all setups/orders for credential {credential_id}")
        return True
    return False



# ==================== Strategy Presets CRUD ====================

async def create_strategy_preset(preset_data: Dict[str, Any]) -> str:
    collection = mongodb.get_db()["strategy_presets"]
    preset = StrategyPreset(**preset_data)
    result = await collection.insert_one(preset.model_dump(by_alias=True, exclude={"id"}))
    return str(result.inserted_id)

async def get_strategy_presets_by_user(user_id: str) -> List[Dict[str, Any]]:
    collection = mongodb.get_db()["strategy_presets"]
    cursor = collection.find({"user_id": user_id})
    return await cursor.to_list(length=100)

async def get_strategy_preset_by_id(preset_id: str) -> Optional[Dict[str, Any]]:
    try:
        collection = mongodb.get_db()["strategy_presets"]
        return await collection.find_one({"_id": ObjectId(preset_id)})
    except Exception as e:
        logger.error(f"Error getting preset by id: {e}")
        return None

async def update_strategy_preset(preset_id: str, update_data: Dict[str, Any]) -> bool:
    """Update a strategy preset's fields."""
    try:
        collection = mongodb.get_db()["strategy_presets"]
        update_data["updated_at"] = datetime.utcnow()
        result = await collection.update_one(
            {"_id": ObjectId(preset_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating preset: {e}")
        return False

async def delete_strategy_preset(preset_id: str, user_id: str) -> bool:
    try:
        collection = mongodb.get_db()["strategy_presets"]
        result = await collection.delete_one({"_id": ObjectId(preset_id), "user_id": user_id})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error deleting preset: {e}")
        return False

async def ensure_default_presets(user_id: str) -> None:
    presets = await get_strategy_presets_by_user(user_id)
    existing_types = {p.get("strategy_type") for p in presets if p.get("is_default")}

    defaults = [
        {
            "strategy_type": "dual_supertrend",
            "preset_name": "[S] Dual ST (P:20,20 / S:10,10)",
            "parameters": {
                "perusu_atr": 20, "perusu_factor": 20.0,
                "sirusu_atr": 10, "sirusu_factor": 10.0
            },
        },
        {
            "strategy_type": "single_supertrend",
            "preset_name": "[S] Single ST (20, 20)",
            "parameters": {"atr_length": 20, "factor": 20.0},
        },
        {
            "strategy_type": "range_breakout_lazybear",
            "preset_name": "[S] Range Breakout LB (EMA:34)",
            "parameters": {"ema_length": 34, "sl_type": "middle", "min_range_candles": 2},
        },
        {
            "strategy_type": "donchian",
            "preset_name": "[S] Donchian Breakout (20)",
            "parameters": {"period": 20},
        },
        {
            "strategy_type": "ohlc_breakout",
            "preset_name": "[S] OHLC Breakout (09:15, 1h)",
            "parameters": {
                "reference_time": "09:15",
                "reference_timeframe": "1h",
                "use_prev_candle": False,
                "sl_type": "opposite",
                "rr_ratio": 2.0,
                "pip_offset_multiplier": 1.0,
                "entry_mode": "confirmation"
            },
        },
        {
            "strategy_type": "evasive_supertrend",
            "preset_name": "[S] Evasive ST (10, 3.0, T:1.0, A:0.5)",
            "parameters": {
                "atr_length": 10,
                "multiplier": 3.0,
                "noise_threshold": 1.0,
                "expansion_alpha": 0.5
            },
        },
    ]

    for d in defaults:
        if d["strategy_type"] not in existing_types:
            await create_strategy_preset({
                "user_id": user_id,
                "preset_name": d["preset_name"],
                "strategy_type": d["strategy_type"],
                "parameters": d["parameters"],
                "is_default": True,
            })

# ==================== Algo Setups CRUD ====================

async def create_algo_setup(setup_data: Dict[str, Any]) -> str:
    """Create new algo setup."""
    try:
        setup = AlgoSetup(**setup_data)
        result = await mongodb.get_db().algo_setups.insert_one(setup.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ Algo setup created: {setup.setup_name}")
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"❌ Failed to create algo setup: {e}")
        raise


async def get_algo_setups_by_user(user_id: str, active_only: bool = False) -> List[Dict[str, Any]]:
    """Get all algo setups for a user."""
    try:
        query = {"user_id": user_id}
        if active_only:
            query["is_active"] = True
        
        cursor = mongodb.get_db().algo_setups.find(query)
        setups = await cursor.to_list(length=100)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"❌ Failed to get algo setups: {e}")
        return []


async def get_algo_setup_by_id(setup_id: str) -> Optional[Dict[str, Any]]:
    """Get algo setup by ID."""
    try:
        setup = await mongodb.get_db().algo_setups.find_one({"_id": ObjectId(setup_id)})
        
        if setup:
            setup["_id"] = str(setup["_id"])
        
        return setup
        
    except Exception as e:
        logger.error(f"❌ Failed to get algo setup by ID: {e}")
        return None


async def update_algo_setup(setup_id: str, update_data: Dict[str, Any]) -> bool:
    """Update algo setup."""
    try:
        update_data["updated_at"] = datetime.utcnow()
        
        result = await mongodb.get_db().algo_setups.update_one(
            {"_id": ObjectId(setup_id)},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"✅ Algo setup updated: {setup_id}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ Failed to update algo setup: {e}")
        return False


# ==================== Archived Setups ====================

async def archive_setup(setup_doc: dict, setup_type: str) -> bool:
    """Archive a setup document before deletion."""
    try:
        db = await get_db()
        archive_doc = {**setup_doc}
        archive_doc.pop("_id", None)
        archive_doc["original_id"] = str(setup_doc.get("_id", ""))
        archive_doc["setup_type"] = setup_type  # "algo" or "screener"
        archive_doc["archived_at"] = datetime.utcnow()
        await mongodb.get_db().archived_setups.update_one(
            {"original_id": archive_doc["original_id"]},
            {"$set": archive_doc},
            upsert=True
        )
        logger.info(f"📦 Archived {setup_type} setup: {setup_doc.get('setup_name', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to archive setup: {e}")
        return False


async def get_archived_setups_by_user(user_id: str, is_paper_trade: bool = False) -> List[Dict[str, Any]]:
    """Get all archived setups for a user, filtered by paper/live mode."""
    try:
        db = await get_db()
        query = {"user_id": user_id, "is_paper_trade": is_paper_trade}
        cursor = mongodb.get_db().archived_setups.find(query).sort("archived_at", -1)
        setups = await cursor.to_list(200)
        for s in setups:
            s["_id"] = str(s["_id"])
        return setups
    except Exception as e:
        logger.error(f"Failed to get archived setups: {e}")
        return []


async def get_archived_setup_by_id(original_id: str) -> Optional[Dict[str, Any]]:
    """Get a single archived setup by its original setup ID."""
    try:
        db = await get_db()
        doc = await mongodb.get_db().archived_setups.find_one({"original_id": original_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception as e:
        logger.error(f"Failed to get archived setup: {e}")
        return None


async def delete_archived_setup_by_name(user_id: str, setup_name: str) -> bool:
    """Delete an archived setup by its name for a user."""
    try:
        db = await get_db()
        result = await mongodb.get_db().archived_setups.delete_many({
            "user_id": user_id,
            "setup_name": setup_name
        })
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Failed to delete archived setup '{setup_name}': {e}")
        return False


async def delete_algo_setup(setup_id: str, user_id: str) -> bool:
    """
    Archive then delete algo setup and cascade delete all related data.
    """
    db = await get_db()
    # Archive the setup before deletion
    setup_doc = await mongodb.get_db().algo_setups.find_one({"_id": ObjectId(setup_id), "user_id": user_id})
    if setup_doc:
        await archive_setup(setup_doc, "algo")
    # Clean up all related DB records first
    await mongodb.get_db().orders.delete_many({"algo_setup_id": setup_id})
    await mongodb.get_db().positions.delete_many({"algo_setup_id": setup_id})
    await mongodb.get_db().trade_states.delete_many({"setup_id": setup_id})
    await mongodb.get_db().position_locks.delete_many({"setup_id": setup_id})
    await mongodb.get_db().indicator_cache.delete_many({"setup_id": setup_id})
    # Delete the setup itself
    result = await mongodb.get_db().algo_setups.delete_one({
        "_id": ObjectId(setup_id),
        "user_id": user_id
    })
    if result.deleted_count > 0:
        logger.info(f"🗑️ Cascade deleted all related records for algo setup {setup_id}")
        return True
    return False

async def get_all_active_algo_setups() -> List[Dict[str, Any]]:
    """Get all active algo setups across all users."""
    try:
        cursor = mongodb.get_db().algo_setups.find({"is_active": True})
        setups = await cursor.to_list(length=1000)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"❌ Failed to get all active algo setups: {e}")
        return []


# ==================== Trade State CRUD (Replaces AlgoActivity) ====================

async def create_trade_state(trade_data: dict) -> str:
    try:
        from database.models import TradeState
        trade = TradeState(**trade_data)
        result = await mongodb.get_db().trade_states.insert_one(trade.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ Trade state created for setup: {trade.setup_name} ({trade.status})")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"❌ Failed to create trade state: {e}")
        raise

async def update_trade_state(trade_id: str, update_data: dict) -> bool:
    try:
        update_data["updated_at"] = datetime.utcnow()
        from bson import ObjectId
        result = await mongodb.get_db().trade_states.update_one(
            {"_id": ObjectId(trade_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"❌ Failed to update trade state: {e}")
        return False

async def get_trade_state_by_id(trade_id: str) -> dict:
    try:
        from bson import ObjectId
        trade = await mongodb.get_db().trade_states.find_one({"_id": ObjectId(trade_id)})
        if trade:
            trade["_id"] = str(trade["_id"])
        return trade
    except Exception as e:
        logger.error(f"❌ Failed to get trade state by ID: {e}")
        return None

async def get_open_trade_states() -> list:
    try:
        cursor = mongodb.get_db().trade_states.find({"status": "open"})
        trades = await cursor.to_list(length=1000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get open trade states: {e}")
        return []

async def get_pending_entry_trade_states() -> list:
    try:
        cursor = mongodb.get_db().trade_states.find({"status": "pending_entry"})
        trades = await cursor.to_list(length=1000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get pending entry trade states: {e}")
        return []

async def get_open_trade_by_setup(setup_id: str) -> dict:
    try:
        trade = await mongodb.get_db().trade_states.find_one({
            "setup_id": setup_id,
            "status": "open"
        })
        if trade:
            trade["_id"] = str(trade["_id"])
        return trade
    except Exception as e:
        logger.error(f"❌ Failed to get open trade by setup: {e}")
        return None

async def get_pending_trade_by_setup(setup_id: str) -> dict:
    try:
        trade = await mongodb.get_db().trade_states.find_one({
            "setup_id": setup_id,
            "status": "pending_entry"
        })
        if trade:
            trade["_id"] = str(trade["_id"])
        return trade
    except Exception as e:
        logger.error(f"❌ Failed to get pending trade by setup: {e}")
        return None

async def get_trades_by_user(user_id: str, closed_only: bool = False, is_paper: bool = False, days: int = None) -> list:
    try:
        from datetime import datetime, timedelta
        query = {"user_id": user_id, "is_paper_trade": is_paper}
        if closed_only:
            query["status"] = "closed"
            if days:
                cutoff = datetime.utcnow() - timedelta(days=days)
                query["$or"] = [
                    {"entry_time": {"$gte": cutoff}},
                    {"exit_time": {"$gte": cutoff}}
                ]
        else:
            if days:
                cutoff = datetime.utcnow() - timedelta(days=days)
                # Show ALL open/pending trades regardless of age, 
                # plus any trades opened OR closed in the last X days.
                query["$or"] = [
                    {"status": {"$in": ["open", "pending_entry"]}},
                    {"entry_time": {"$gte": cutoff}},
                    {"exit_time": {"$gte": cutoff}}
                ]
            
        cursor = mongodb.get_db().trade_states.find(query).sort("entry_time", -1)
        trades = await cursor.to_list(length=1000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get user trades: {e}")
        return []

async def delete_closed_paper_trades(user_id: str) -> bool:
    """Deletes all closed and cancelled paper trades for a user from trade_states."""
    try:
        await mongodb.get_db().trade_states.delete_many({
            "user_id": user_id,
            "is_paper_trade": True,
            "status": {"$in": ["closed", "cancelled"]}
        })
        return True
    except Exception as e:
        logger.error(f"❌ Failed to delete closed paper trades: {e}")
        return False

# ==================== Indicator Cache CRUD ====================

async def save_indicator_cache(cache_data: dict) -> bool:
    try:
        from database.models import IndicatorCache
        cache = IndicatorCache(**cache_data)
        await mongodb.get_db().indicator_cache.update_one(
            {
                "setup_id": cache.setup_id,
                "asset": cache.asset,
                "timeframe": cache.timeframe
            },
            {"$set": cache.dict(by_alias=True, exclude={"id"})},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save indicator cache: {e}")
        return False

async def get_last_primary_signal(setup_id: str, asset: str, timeframe: str) -> int | None:
    """Fetch the previously cached primary_signal for a setup before it gets overwritten.
    DEPRECATED: Use get_last_strategy_state() instead. Kept for backwards compat.
    """
    state = await get_last_strategy_state(setup_id, asset, timeframe)
    if state and "primary_signal" in state:
        return state["primary_signal"]
    # Backwards compat
    if state and "perusu_signal" in state:
        return state["perusu_signal"]
    return None


async def get_last_strategy_state(setup_id: str, asset: str, timeframe: str) -> dict | None:
    """Fetch the previously cached strategy_state dict for a setup before it gets overwritten.
    
    This is the generic replacement for get_last_primary_signal().
    Each strategy stores whatever state it needs in strategy_state (e.g., {"primary_signal": 1}).
    The engine fetches this before saving new cache, then passes it to generate_entry_signal().
    """
    try:
        cache = await mongodb.get_db().indicator_cache.find_one({
            "setup_id": setup_id,
            "asset": asset,
            "timeframe": timeframe
        })
        if cache and "strategy_state" in cache:
            return cache["strategy_state"]
        # Backwards compat: if no strategy_state but has primary_signal or perusu_signal, synthesize one
        if cache and "primary_signal" in cache:
            return {"primary_signal": cache["primary_signal"]}
        if cache and "perusu_signal" in cache:
            return {"primary_signal": cache["perusu_signal"]}
        return None
    except Exception as e:
        logger.error(f"❌ Failed to get last strategy state: {e}")
        return None


async def get_indicator_cache_by_type(setup_type: str, is_paper_trade: bool) -> list:
    try:
        from datetime import datetime, timedelta
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        
        cursor = mongodb.get_db().indicator_cache.find({
            "setup_type": setup_type,
            "is_paper_trade": is_paper_trade,
            "calculated_at": {"$gte": one_hour_ago}
        }).sort("asset", 1)
        
        caches = await cursor.to_list(length=1000)
        return caches
    except Exception as e:
        logger.error(f"❌ Failed to get indicator caches: {e}")
        return []


# ==================== Screener Setups CRUD ====================

async def create_screener_setup(setup_data: Dict[str, Any]) -> str:
    """Create new screener setup."""
    try:
        result = await mongodb.get_db().screener_setups.insert_one(setup_data)
        logger.info(f"✅ Screener setup created: {setup_data['setup_name']}")
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"❌ Failed to create screener setup: {e}")
        raise


async def get_screener_setups_by_user(user_id: str, active_only: bool = False) -> List[Dict[str, Any]]:
    """Get all screener setups for a user."""
    try:
        query = {"user_id": user_id}
        if active_only:
            query["is_active"] = True
        
        cursor = mongodb.get_db().screener_setups.find(query)
        setups = await cursor.to_list(length=100)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"❌ Failed to get screener setups: {e}")
        return []


async def get_screener_setup_by_id(setup_id: str) -> Optional[Dict[str, Any]]:
    """Get screener setup by ID."""
    try:
        setup = await mongodb.get_db().screener_setups.find_one({"_id": ObjectId(setup_id)})
        
        if setup:
            setup["_id"] = str(setup["_id"])
        
        return setup
        
    except Exception as e:
        logger.error(f"❌ Failed to get screener setup by ID: {e}")
        return None


async def update_screener_setup(setup_id: str, update_data: Dict[str, Any]) -> bool:
    """Update screener setup."""
    try:
        update_data["updated_at"] = datetime.utcnow()
        
        result = await mongodb.get_db().screener_setups.update_one(
            {"_id": ObjectId(setup_id)},
            {"$set": update_data}
        )
        
        return result.modified_count > 0
        
    except Exception as e:
        logger.error(f"❌ Failed to update screener setup: {e}")
        return False


async def delete_screener_setup(setup_id: str, user_id: str) -> bool:
    """Archive then delete screener setup and cascade delete all related data."""
    try:
        db = await get_db()
        # Archive the setup before deletion
        setup_doc = await mongodb.get_db().screener_setups.find_one({"_id": ObjectId(setup_id), "user_id": user_id})
        if setup_doc:
            await archive_setup(setup_doc, "screener")
        # Clean up all related DB records first
        await mongodb.get_db().orders.delete_many({"algo_setup_id": setup_id})
        await mongodb.get_db().positions.delete_many({"algo_setup_id": setup_id})
        await mongodb.get_db().trade_states.delete_many({"setup_id": setup_id})
        await mongodb.get_db().position_locks.delete_many({"setup_id": setup_id})
        await mongodb.get_db().indicator_cache.delete_many({"setup_id": setup_id})
        await mongodb.get_db().screener_indicator_cache.delete_many({"screener_setup_id": setup_id})
        
        result = await mongodb.get_db().screener_setups.delete_one({
            "_id": ObjectId(setup_id),
            "user_id": user_id
        })
        
        if result.deleted_count > 0:
            logger.info(f"✅ Screener setup deleted: {setup_id}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ Failed to delete screener setup: {e}")
        return False


async def get_all_active_screener_setups() -> List[Dict[str, Any]]:
    """Get all active screener setups."""
    try:
        cursor = mongodb.get_db().screener_setups.find({"is_active": True})
        setups = await cursor.to_list(length=1000)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"❌ Failed to get all active screener setups: {e}")
        return []
        

# ==================== Position Locks CRUD (✅ NEW) ====================

async def acquire_position_lock(db: AsyncIOMotorDatabase,
                               symbol: str,
                               setup_id: str,
                               setup_name: str,
                               api_id: str = "") -> bool:
    """
    Acquire exclusive lock on asset for this setup within an API account.
    Only ONE setup can trade this asset per API account at a time.
    Uses compound key (symbol, api_id) so the same asset can be traded
    on different exchange accounts simultaneously.
    Uses UPSERT to prevent duplicate key errors on restart.
    
    Args:
        db: MongoDB database connection
        symbol: Asset symbol (e.g., "ADAUSD")
        setup_id: Setup ID requesting lock
        setup_name: Setup name (for logging)
        api_id: API credential ID (compound key with symbol)
    
    Returns:
        True if lock acquired, False if already locked by another setup
    """
    try:
        collection = mongodb.get_db()["position_locks"]
        
        # Check if locked by a DIFFERENT setup on the same API account
        existing = await collection.find_one({"symbol": symbol, "api_id": api_id})
        if existing and existing.get("setup_id") != setup_id:
            logger.error(f"❌ {symbol} is LOCKED by: {existing.get('setup_name')} (api_id={api_id})")
            return False
        
        # Upsert: update if exists (same setup), insert if new
        lock_data = {
            "symbol": symbol,
            "api_id": api_id,
            "setup_id": setup_id,
            "setup_name": setup_name,
            "locked_at": datetime.utcnow()
        }
        
        result = await collection.update_one(
            {"symbol": symbol, "api_id": api_id},
            {"$set": lock_data},
            upsert=True
        )
        
        logger.info(f"✅ Lock acquired/refreshed for {symbol} by {setup_name} (api_id={api_id})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to acquire lock for {symbol}: {e}")
        return False


async def get_position_lock(db: AsyncIOMotorDatabase,
                           symbol: str,
                           api_id: str = "") -> Optional[dict]:
    """
    Get lock information for an asset on a specific API account.
    
    Args:
        db: MongoDB database connection
        symbol: Asset symbol
        api_id: API credential ID (compound key with symbol)
    
    Returns:
        Lock record or None if not locked
    """
    try:
        collection = mongodb.get_db()["position_locks"]
        lock = await collection.find_one({"symbol": symbol, "api_id": api_id})
        return lock
        
    except Exception as e:
        logger.error(f"❌ Error getting lock: {e}")
        return None


async def release_position_lock(db: AsyncIOMotorDatabase,
                               symbol: str,
                               setup_id: str,
                               api_id: str = "") -> bool:
    """
    Release lock when position is closed.
    Uses compound key (symbol, api_id) + setup_id for safety.
    
    Args:
        db: MongoDB database connection
        symbol: Asset symbol
        setup_id: Setup ID releasing lock
        api_id: API credential ID (compound key with symbol)
    
    Returns:
        True if lock released, False if error
    """
    try:
        logger.debug(f"release_position_lock called for symbol={symbol}, setup_id={setup_id}, api_id={api_id}")
        collection = mongodb.get_db()["position_locks"]
        
        result = await collection.delete_one({
            "symbol": symbol,
            "api_id": api_id,
            "setup_id": setup_id
        })
        logger.debug(f"release_position_lock deleted_count={result.deleted_count} for symbol={symbol}, setup_id={setup_id}, api_id={api_id}")
        if result.deleted_count > 0:
            logger.info(f"✅ Released lock on {symbol} (api_id={api_id})")
            return True
        else:
            logger.warning(f"⚠️ Lock not found for {symbol} (api_id={api_id})")
            return False
        
    except Exception as e:
        logger.error(f"❌ Error releasing lock: {e}")
        return False


async def cleanup_stale_locks(db: AsyncIOMotorDatabase,
                             max_age_minutes: int = 60) -> int:
    """
    ✅ NEW: Clean up stale locks (in case setup crashes without releasing).
    
    Args:
        db: MongoDB database connection
        max_age_minutes: Max age of lock before cleanup
    
    Returns:
        Number of locks cleaned up
    """
    try:
        collection = mongodb.get_db()["position_locks"]
        cutoff_time = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        
        result = await collection.delete_many({
            "locked_at": {"$lt": cutoff_time}
        })
        
        if result.deleted_count > 0:
            logger.warning(f"🧹 Cleaned up {result.deleted_count} stale locks")
        
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"❌ Error cleaning locks: {e}")
        return 0

async def get_api_credentials_by_user_decrypted(user_id: str) -> List[Dict[str, Any]]:
    """Get all API credentials for a user WITH DECRYPTED KEYS."""
    try:
        cursor = mongodb.get_db().api_credentials.find({"user_id": user_id})
        credentials = await cursor.to_list(length=100)
        
        # Convert ObjectId to string and DECRYPT values
        for cred in credentials:
            cred["_id"] = str(cred["_id"])
            try:
                # Decrypt sensitive data
                cred["api_key"] = cipher_suite.decrypt(cred["api_key"].encode()).decode()
                cred["api_secret"] = cipher_suite.decrypt(cred["api_secret"].encode()).decode()
            except Exception as e:
                logger.error(f"❌ Failed to decrypt credential for {cred.get('api_name','UNKNOWN')}: {e}")
                cred["api_key"] = None
                cred["api_secret"] = None
        
        return credentials
    except Exception as e:
        logger.error(f"❌ Failed to get decrypted API credentials: {e}")
        return []

async def create_order_record(order_data: dict) -> str:
    """Create a new order record for robust tracking."""
    try:
        order = OrderRecord(**order_data)
        result = await mongodb.get_db().orders.insert_one(order.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ Order record created: {order.order_id}")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"❌ Failed to create order record: {e}")
        return ""

async def update_order_record(order_id: int, update_data: dict) -> bool:
    """Update an order record, e.g. status, fill time/price, cancelation, etc."""
    try:
        update_data["updated_at"] = datetime.utcnow()
        result = await mongodb.get_db().orders.update_one(
            {"order_id": order_id},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"❌ Failed to update order record: {e}")
        return False

async def create_position_record(position_data: dict) -> str:
    """
    Save a new position to the `positions` collection.
    """
    db = await get_db()
    result = await mongodb.get_db().positions.insert_one(position_data)
    return str(result.inserted_id)
    
async def create_position_lock(symbol: str, setup_id: str, api_id: str = "") -> bool:
    """
    Shortcut compatible with reconciliation routine.
    """
    db = await get_db()
    return await acquire_position_lock(db, symbol, setup_id, setup_name=setup_id, api_id=api_id)

async def delete_position_lock(symbol: str = None) -> int:
    """
    If symbol is None, deletes ALL locks (startup cleanup).
    If symbol given, deletes single lock.
    """
    logger.debug(f"delete_position_lock called with symbol={symbol}")
    db = await get_db()
    collection = mongodb.get_db()["position_locks"]
    if symbol:
        result = await collection.delete_one({"symbol": symbol})
        logger.debug(f"delete_position_lock deleted_count={result.deleted_count} for symbol={symbol}")
        return result.deleted_count
    else:
        result = await collection.delete_many({})
        logger.debug(f"delete_position_lock deleted ALL locks, deleted_count={result.deleted_count}")
        return result.deleted_count
        
async def get_screener_positions_by_asset(
    asset: str,
    is_paper: bool = None,
    api_id: str = None
) -> list:
    """Get all open/pending screener trade states for an asset.
    
    Queries `trade_states` (not the deprecated `positions` collection).
    
    Args:
        asset: Asset symbol (e.g., "BTCUSD")
        is_paper: Filter by paper/real mode. None = all.
        api_id: Filter by API credential ID. None = all.
    """
    db = await get_db()
    query = {
        "asset": asset,
        "status": {"$in": ["open", "pending_entry"]},
        "setup_type": "screener"
    }
    if is_paper is not None:
        query["is_paper_trade"] = is_paper
    if api_id is not None:
        query["api_id"] = api_id
    trades = await mongodb.get_db().trade_states.find(query).to_list(None)
    for t in trades:
        t["_id"] = str(t["_id"])
    return trades

async def create_screener_position_record(position_data: Dict[str, Any]):
    """Create a screener position record."""
    db = await get_db()
    result = await mongodb.get_db().positions.insert_one(position_data)
    logger.info(f"✅ Screener position record created: {result.inserted_id}")
    return result.inserted_id

async def get_screener_indicator_cache(screener_setup_id: str, asset: str, indicator_name: str):
    """Get cached indicator for screener asset."""
    db = await get_db()
    cache = await mongodb.get_db().screener_indicator_cache.find_one({
        "screener_setup_id": screener_setup_id,
        "asset": asset,
        "indicator_name": indicator_name
    })
    return cache

async def upsert_screener_indicator_cache(cache_data: Dict[str, Any]):
    """Update or insert screener indicator cache."""
    db = await get_db()
    await mongodb.get_db().screener_indicator_cache.update_one(
        {
            "screener_setup_id": cache_data["screener_setup_id"],
            "asset": cache_data["asset"],
            "indicator_name": cache_data["indicator_name"]
        },
        {"$set": cache_data},
        upsert=True
    )


# ==================== Paper Trading CRUD ====================

async def get_paper_balance(user_id: str) -> Optional[Dict[str, Any]]:
    """Get paper trading balance for a user. Creates default if not exists."""
    try:
        balance = await mongodb.get_db().paper_balances.find_one({"user_id": user_id})
        
        if not balance:
            # Create default paper balance
            default_balance = PaperBalance(user_id=user_id)
            result = await mongodb.get_db().paper_balances.insert_one(
                default_balance.dict(by_alias=True, exclude={"id"})
            )
            balance = await mongodb.get_db().paper_balances.find_one({"_id": result.inserted_id})
            logger.info(f"Created default paper balance for user {user_id}")
        
        if balance:
            balance["_id"] = str(balance["_id"])
        
        return balance
        
    except Exception as e:
        logger.error(f"Failed to get paper balance: {e}")
        return None


async def update_paper_balance(user_id: str, update_data: Dict[str, Any]) -> bool:
    """Update paper trading balance."""
    try:
        update_data["updated_at"] = datetime.utcnow()
        
        result = await mongodb.get_db().paper_balances.update_one(
            {"user_id": user_id},
            {"$set": update_data}
        )
        
        return result.modified_count > 0
        
    except Exception as e:
        logger.error(f"Failed to update paper balance: {e}")
        return False


async def reset_paper_balance(user_id: str, new_balance: float = 10000.0) -> bool:
    """Reset paper trading balance to starting amount."""
    try:
        reset_data = {
            "balance": new_balance,
            "initial_balance": new_balance,
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "total_pnl": 0.0,
            "total_fees": 0.0,
            "locked_margin": 0.0,
            "updated_at": datetime.utcnow(),
            "last_reset_at": datetime.utcnow()
        }
        
        result = await mongodb.get_db().paper_balances.update_one(
            {"user_id": user_id},
            {"$set": reset_data},
            upsert=True
        )
        
        logger.info(f"Paper balance reset to ${new_balance} for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to reset paper balance: {e}")
        return False


async def get_paper_trade_activities(
    user_id: str, 
    days: Optional[int] = None,
    closed_only: bool = False
) -> List[Dict[str, Any]]:
    """Get paper trade activities for a user."""
    try:
        query = {"user_id": user_id, "is_paper_trade": True}
        
        if days:
            cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            query["trade_date"] = {"$gte": cutoff_date}
        
        if closed_only:
            query["status"] = "closed"
        
        cursor = mongodb.get_db().trade_states.find(query).sort("entry_time", -1)
        activities = await cursor.to_list(length=5000)
        
        for activity in activities:
            activity["_id"] = str(activity["_id"])
        
        return activities
        
    except Exception as e:
        logger.error(f"Failed to get paper trade activities: {e}")
        return []


async def get_real_trade_activities(
    user_id: str,
    days: Optional[int] = None,
    closed_only: bool = False
) -> List[Dict[str, Any]]:
    """Get real trade activities for a user (excludes paper trades)."""
    try:
        query = {
            "user_id": user_id,
            "$or": [
                {"is_paper_trade": False},
                {"is_paper_trade": {"$exists": False}}
            ]
        }
        
        if days:
            cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            query["trade_date"] = {"$gte": cutoff_date}
        
        if closed_only:
            query["status"] = "closed"
        
        cursor = mongodb.get_db().trade_states.find(query).sort("entry_time", -1)
        activities = await cursor.to_list(length=5000)
        
        for activity in activities:
            activity["_id"] = str(activity["_id"])
        
        return activities
        
    except Exception as e:
        logger.error(f"Failed to get real trade activities: {e}")
        return []


async def get_open_paper_positions(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all open paper trade positions (for monitoring loop)."""
    try:
        query = {"is_paper_trade": True, "status": "open"}
        if user_id:
            query["user_id"] = user_id
        
        cursor = mongodb.get_db().trade_states.find(query)
        positions = await cursor.to_list(length=1000)
        
        for pos in positions:
            pos["_id"] = str(pos["_id"])
        
        return positions
        
    except Exception as e:
        logger.error(f"Failed to get open paper positions: {e}")
        return []


async def get_algo_setups_by_paper_mode(
    user_id: str, 
    is_paper: bool, 
    active_only: bool = False
) -> List[Dict[str, Any]]:
    """Get algo setups filtered by paper/real mode."""
    try:
        query = {"user_id": user_id}
        
        if is_paper:
            query["is_paper_trade"] = True
        else:
            query["$or"] = [
                {"is_paper_trade": False},
                {"is_paper_trade": {"$exists": False}}
            ]
        
        if active_only:
            query["is_active"] = True
        
        cursor = mongodb.get_db().algo_setups.find(query)
        setups = await cursor.to_list(length=100)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"Failed to get algo setups by paper mode: {e}")
        return []


async def get_screener_setups_by_paper_mode(
    user_id: str,
    is_paper: bool,
    active_only: bool = False
) -> List[Dict[str, Any]]:
    """Get screener setups filtered by paper/real mode."""
    try:
        query = {"user_id": user_id}
        
        if is_paper:
            query["is_paper_trade"] = True
        else:
            query["$or"] = [
                {"is_paper_trade": False},
                {"is_paper_trade": {"$exists": False}}
            ]
        
        if active_only:
            query["is_active"] = True
        
        cursor = mongodb.get_db().screener_setups.find(query)
        setups = await cursor.to_list(length=100)
        
        for setup in setups:
            setup["_id"] = str(setup["_id"])
        
        return setups
        
    except Exception as e:
        logger.error(f"Failed to get screener setups by paper mode: {e}")
        return []


# ==================== CUSTOM LISTS ====================

async def get_custom_list(list_name: str) -> list:
    """Get a custom token list by name."""
    doc = await mongodb.get_db().custom_lists.find_one({"list_name": list_name})
    if doc and "tokens" in doc:
        return doc["tokens"]
    return []

async def update_custom_list(list_name: str, tokens: list) -> bool:
    """Update or create a custom token list."""
    try:
        # Ensure uppercase and stripped
        tokens = [t.strip().upper() for t in tokens if t.strip()]
        # Remove duplicates while preserving order
        tokens = list(dict.fromkeys(tokens))
        
        await mongodb.get_db().custom_lists.update_one(
            {"list_name": list_name},
            {"$set": {"tokens": tokens, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Error updating custom list {list_name}: {e}")
        return False


# ==================== BACKTEST RESULTS ====================

async def save_backtest_result(result_data: dict) -> Optional[str]:
    """
    Save a completed backtest result to MongoDB.

    Args:
        result_data: Dictionary matching the BacktestResult schema.

    Returns:
        The inserted document's string ID, or None on failure.
    """
    try:
        db = mongodb.get_db()
        # Ensure created_at is set
        if "created_at" not in result_data:
            result_data["created_at"] = datetime.utcnow()

        inserted = await db.backtest_results.insert_one(result_data)
        logger.info(f"[BT-CRUD] Saved backtest result: {inserted.inserted_id}")
        return str(inserted.inserted_id)
    except Exception as e:
        logger.error(f"[BT-CRUD] Error saving backtest result: {e}")
        return None


async def get_backtest_results(
    user_id: str,
    sort_by: str = "created_at",
    sort_order: int = -1,
    limit: int = 20,
    skip: int = 0,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    profit_only: bool = False,
) -> tuple[list, int]:
    """
    Retrieve past backtest results for a user with true database pagination.

    Args:
        user_id:     Telegram user ID.
        sort_by:     Field to sort by. Supported values:
                     "created_at", "overall_profit", "overall_profit_pct",
                     "win_pct", "max_drawdown", "num_trades",
                     "sharpe_ratio", "expectancy_ratio", "reward_to_risk".
        sort_order:  1 for ascending, -1 for descending.
        limit:       Max results to return (default 20).
        skip:        Number of results to skip for pagination.
        symbol:      Optional filter by symbol (e.g. "BTCUSD").
        strategy:    Optional filter by strategy name.

    Returns:
        Tuple of (List of backtest result dicts, Total count of matching documents).
    """
    try:
        db = mongodb.get_db()
        query = {"user_id": user_id}

        if symbol:
            query["symbol"] = symbol.upper()
        if strategy:
            query["strategy"] = strategy
        if profit_only:
            query["overall_profit_pct"] = {"$gt": 0}

        # Validate sort field to prevent injection
        allowed_sort_fields = {
            "created_at", "overall_profit", "overall_profit_pct",
            "win_pct", "max_drawdown", "max_drawdown_pct",
            "num_trades", "sharpe_ratio", "expectancy_ratio",
            "reward_to_risk", "return_over_max_dd", "profit_factor",
            "max_win_streak", "max_loss_streak", "final_balance",
        }
        if sort_by not in allowed_sort_fields:
            sort_by = "created_at"

        total_count = await db.backtest_results.count_documents(query)

        # Apply MongoDB Projection to exclude heavy array data for fast menu rendering
        cursor = db.backtest_results.find(
            query, 
            {"trade_log": 0, "equity_curve": 0}
        ).sort(sort_by, sort_order).skip(skip).limit(limit)
        results = await cursor.to_list(length=limit)

        # Convert ObjectId to string for JSON safety
        for r in results:
            r["_id"] = str(r["_id"])

        return results, total_count
    except Exception as e:
        logger.error(f"[BT-CRUD] Error fetching backtest results: {e}")
        return [], 0


async def get_backtest_result_by_id(result_id: str, include_arrays: bool = False) -> Optional[dict]:
    """
    Retrieve a single backtest result by its MongoDB _id.

    Args:
        result_id: The string representation of the document's ObjectId.
        include_arrays: Whether to download heavy trade_log and equity_curve arrays.

    Returns:
        The backtest result dict, or None if not found.
    """
    try:
        db = mongodb.get_db()
        projection = None if include_arrays else {"trade_log": 0, "equity_curve": 0}
        doc = await db.backtest_results.find_one({"_id": ObjectId(result_id)}, projection)
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception as e:
        logger.error(f"[BT-CRUD] Error fetching backtest result {result_id}: {e}")
        return None


async def delete_backtest_result(result_id: str) -> bool:
    """
    Delete a single backtest result by its MongoDB _id.

    Args:
        result_id: The string representation of the document's ObjectId.

    Returns:
        True if deleted, False otherwise.
    """
    try:
        db = mongodb.get_db()
        result = await db.backtest_results.delete_one({"_id": ObjectId(result_id)})
        if result.deleted_count > 0:
            logger.info(f"[BT-CRUD] Deleted backtest result: {result_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"[BT-CRUD] Error deleting backtest result {result_id}: {e}")
        return False

async def delete_all_backtest_results(user_id: str) -> int:
    """
    Delete all backtest results for a user.
    """
    try:
        db = mongodb.get_db()
        result = await db.backtest_results.delete_many({"user_id": user_id})
        return result.deleted_count
    except Exception as e:
        logger.error(f"[BT-CRUD] Error deleting all results for {user_id}: {e}")
        return 0


async def delete_all_backtest_results(user_id: str) -> int:
    """
    Delete all backtest results for a user.

    Args:
        user_id: Telegram user ID.

    Returns:
        Number of documents deleted.
    """
    try:
        db = mongodb.get_db()
        result = await db.backtest_results.delete_many({"user_id": user_id})
        logger.info(f"[BT-CRUD] Deleted {result.deleted_count} backtest results for user {user_id}")
        return result.deleted_count
    except Exception as e:
        logger.error(f"[BT-CRUD] Error deleting all backtest results: {e}")
        return 0


async def get_backtest_summary(user_id: str) -> dict:
    """
    Get a quick summary of a user's backtest history.

    Returns:
        {
            "total_backtests": int,
            "best_profit_pct": float,
            "worst_drawdown_pct": float,
            "avg_win_rate": float,
            "symbols_tested": list[str],
        }
    """
    try:
        db = mongodb.get_db()
        # Apply MongoDB Projection to exclude heavy array data for fast calculation
        cursor = db.backtest_results.find(
            {"user_id": user_id}, 
            {"trade_log": 0, "equity_curve": 0}
        )
        results = await cursor.to_list(length=500)

        if not results:
            return {
                "total_backtests": 0,
                "best_profit_pct": 0.0,
                "worst_drawdown_pct": 0.0,
                "avg_win_rate": 0.0,
                "symbols_tested": [],
            }

        symbols = list(set(r.get("symbol", "") for r in results))
        profits = [r.get("overall_profit_pct", 0.0) for r in results]
        drawdowns = [r.get("max_drawdown_pct", 0.0) for r in results]
        win_rates = [r.get("win_pct", 0.0) for r in results]

        return {
            "total_backtests": len(results),
            "best_profit_pct": max(profits) if profits else 0.0,
            "worst_drawdown_pct": min(drawdowns) if drawdowns else 0.0,
            "avg_win_rate": sum(win_rates) / len(win_rates) if win_rates else 0.0,
            "symbols_tested": symbols,
        }
    except Exception as e:
        logger.error(f"[BT-CRUD] Error getting backtest summary: {e}")
        return {"total_backtests": 0, "best_profit_pct": 0.0,
                "worst_drawdown_pct": 0.0, "avg_win_rate": 0.0,
                "symbols_tested": []}


async def setup_backtest_indexes():
    """
    Create MongoDB indexes on the backtest_results collection
    for efficient sorting and filtering.

    Should be called once during bot startup.
    """
    try:
        db = mongodb.get_db()
        collection = db.backtest_results

        # Index for listing results by user (most common query)
        await collection.create_index([("user_id", 1), ("created_at", -1)])

        # Sorting indexes
        await collection.create_index([("user_id", 1), ("overall_profit", -1)])
        await collection.create_index([("user_id", 1), ("win_pct", -1)])
        await collection.create_index([("user_id", 1), ("max_drawdown", 1)])
        await collection.create_index([("user_id", 1), ("sharpe_ratio", -1)])

        # Filter indexes
        await collection.create_index([("user_id", 1), ("symbol", 1)])
        await collection.create_index([("user_id", 1), ("strategy", 1)])

        logger.info("[BT-CRUD] Backtest result indexes created successfully")
    except Exception as e:
        logger.error(f"[BT-CRUD] Error creating backtest indexes: {e}")
