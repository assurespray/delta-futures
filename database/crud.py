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
from database.models import OrderRecord

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
    setups = await db.algo_setups.find({"api_id": credential_id}).to_list(100)

    # For each setup, delete all related records by setup ID
    for setup in setups:
        setup_id = str(setup["_id"])
        await db.orders.delete_many({"algo_setup_id": setup_id})
        await db.positions.delete_many({"algo_setup_id": setup_id})
        await db.trade_states.delete_many({"algo_setup_id": setup_id})
        await db.position_locks.delete_many({"setup_id": setup_id})

    # Delete setups linked to this credential
    await db.algo_setups.delete_many({"api_id": credential_id})

    # Finally, delete the credential itself
    result = await db.api_credentials.delete_one({
        "_id": ObjectId(credential_id),
        "user_id": user_id
    })

    if result.deleted_count > 0:
        logger.info(f"🗑️ Cascade deleted all setups/orders for credential {credential_id}")
        return True
    return False


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


async def delete_algo_setup(setup_id: str, user_id: str) -> bool:
    """
    Delete algo setup and cascade delete all related data (orders, positions, activities, locks).
    """
    db = await get_db()
    # Clean up all related DB records first
    await db.orders.delete_many({"algo_setup_id": setup_id})
    await db.positions.delete_many({"algo_setup_id": setup_id})
    await db.trade_states.delete_many({"algo_setup_id": setup_id})
    await db.position_locks.delete_many({"setup_id": setup_id})  # If you lock by setup_id
    # Delete the setup itself
    result = await db.algo_setups.delete_one({
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



# ==================== Trade State CRUD (Replaces TradeState) ====================

async def create_trade_state(trade_data: Dict[str, Any]) -> str:
    """Create new trade state record."""
    try:
        trade = TradeState(**trade_data)
        result = await mongodb.get_db().trade_states.insert_one(trade.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ Trade state created for setup: {trade.setup_name} ({trade.status})")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"❌ Failed to create trade state: {e}")
        raise

async def update_trade_state(trade_id: str, update_data: Dict[str, Any]) -> bool:
    """Update trade state."""
    try:
        update_data["updated_at"] = datetime.utcnow()
        result = await mongodb.get_db().trade_states.update_one(
            {"_id": ObjectId(trade_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"❌ Failed to update trade state: {e}")
        return False

async def get_trade_state_by_id(trade_id: str) -> Optional[Dict[str, Any]]:
    """Get trade state by ID."""
    try:
        trade = await mongodb.get_db().trade_states.find_one({"_id": ObjectId(trade_id)})
        if trade:
            trade["_id"] = str(trade["_id"])
        return trade
    except Exception as e:
        logger.error(f"❌ Failed to get trade state by ID: {e}")
        return None

async def get_open_trade_states() -> List[Dict[str, Any]]:
    """Get all currently open trade states (real and paper)."""
    try:
        cursor = mongodb.get_db().trade_states.find({"status": "open"})
        trades = await cursor.to_list(length=1000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get open trade states: {e}")
        return []

async def get_pending_entry_trade_states() -> List[Dict[str, Any]]:
    """Get all pending entry trade states (real and paper)."""
    try:
        cursor = mongodb.get_db().trade_states.find({"status": "pending_entry"})
        trades = await cursor.to_list(length=1000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get pending entry trade states: {e}")
        return []

async def get_open_trade_by_setup(setup_id: str) -> Optional[Dict[str, Any]]:
    """Get open trade for a specific setup."""
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

async def get_pending_trade_by_setup(setup_id: str) -> Optional[Dict[str, Any]]:
    """Get pending trade for a specific setup."""
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

async def get_trades_by_user(user_id: str, closed_only: bool = False, is_paper: bool = False, days: int = None) -> List[Dict[str, Any]]:
    """Get historical/active trades for a user."""
    try:
        from datetime import datetime, timedelta
        query = {"user_id": user_id, "is_paper_trade": is_paper}
        if closed_only:
            query["status"] = "closed"
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            query["trade_date"] = {"$gte": cutoff}
        
        cursor = mongodb.get_db().trade_states.find(query).sort("created_at", -1)
        trades = await cursor.to_list(length=5000)
        for t in trades:
            t["_id"] = str(t["_id"])
        return trades
    except Exception as e:
        logger.error(f"❌ Failed to get trades by user: {e}")
        return []


# ==================== Indicator Cache CRUD ====================

async def upsert_indicator_cache(cache_data: Dict[str, Any]) -> bool:
    """Update or insert indicator cache."""
    try:
        result = await mongodb.get_db().indicator_cache.update_one(
            {
                "algo_setup_id": cache_data["algo_setup_id"],
                "indicator_name": cache_data["indicator_name"]
            },
            {"$set": cache_data},
            upsert=True
        )
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to upsert indicator cache: {e}")
        return False


async def get_indicator_cache(algo_setup_id: str, indicator_name: str) -> Optional[Dict[str, Any]]:
    """Get cached indicator data."""
    try:
        cache = await mongodb.get_db().indicator_cache.find_one({
            "algo_setup_id": algo_setup_id,
            "indicator_name": indicator_name
        })
        
        if cache:
            cache["_id"] = str(cache["_id"])
        
        return cache
        
    except Exception as e:
        logger.error(f"❌ Failed to get indicator cache: {e}")
        return None

async def save_indicator_cache(
    algo_setup_id: str,
    indicator_name: str,
    asset: str,
    timeframe: str,
    signal: int,
    value: float
) -> dict:
    """
    Save indicator to cache with signal flip detection.
    Returns dict with flip details:
        {
            "flip": True/False,
            "old_signal": int or None,
            "new_signal": int,
            "old_signal_text": str,
            "new_signal_text": str
        }
    """
    try:
        db = mongodb.get_db()
        
        # 1. Get previous cache entry (if exists)
        previous_cache = await db.indicator_cache.find_one({
            "algo_setup_id": algo_setup_id,
            "indicator_name": indicator_name,
            "asset": asset,
            "timeframe": timeframe
        })
        
        # Extract previous signal
        previous_signal = previous_cache.get("last_signal") if previous_cache else None
        
        # 2. Create new cache document
        cache_doc = {
            "algo_setup_id": algo_setup_id,
            "indicator_name": indicator_name,
            "asset": asset,
            "timeframe": timeframe,
            "calculated_at": datetime.utcnow(),
            "last_signal": signal,              # Current signal
            "previous_signal": previous_signal,  # Track previous for flip detection
            "last_value": value
        }
        
        # 3. Upsert to database
        await db.indicator_cache.update_one(
            {
                "algo_setup_id": algo_setup_id,
                "indicator_name": indicator_name,
                "asset": asset,
                "timeframe": timeframe
            },
            {"$set": cache_doc},
            upsert=True
        )
        
        # 4. Detect flip and build result
        def _signal_text(sig):
            if sig == 1:
                return "Uptrend"
            elif sig == -1:
                return "Downtrend"
            return "Unknown"
        
        flip_occurred = False
        if previous_signal is not None and previous_signal != signal:
            flip_occurred = True
            logger.info(
                f"🔄 SIGNAL FLIP: {indicator_name} for {asset} {timeframe} "
                f"({previous_signal} → {signal})"
            )
        
        return {
            "flip": flip_occurred,
            "old_signal": previous_signal,
            "new_signal": signal,
            "old_signal_text": _signal_text(previous_signal),
            "new_signal_text": _signal_text(signal),
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to save indicator cache: {e}")
        return {"flip": False, "old_signal": None, "new_signal": signal,
                "old_signal_text": "Unknown", "new_signal_text": "Unknown"}

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
    """Delete screener setup and cascade delete all related data."""
    try:
        db = await get_db()
        # Clean up all related DB records first
        await db.orders.delete_many({"algo_setup_id": setup_id})
        await db.positions.delete_many({"algo_setup_id": setup_id})
        await db.trade_states.delete_many({"algo_setup_id": setup_id})
        await db.position_locks.delete_many({"setup_id": setup_id})
        
        result = await db.screener_setups.delete_one({
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
                               setup_name: str) -> bool:
    """
    ✅ Acquire exclusive lock on asset for this setup.
    Only ONE setup can trade this asset at a time.
    Uses UPSERT to prevent duplicate key errors on restart.
    
    Args:
        db: MongoDB database connection
        symbol: Asset symbol (e.g., "ADAUSD")
        setup_id: Setup ID requesting lock
        setup_name: Setup name (for logging)
    
    Returns:
        True if lock acquired, False if already locked by another setup
    """
    try:
        collection = db["position_locks"]
        
        # Check if locked by a DIFFERENT setup
        existing = await collection.find_one({"symbol": symbol})
        if existing and existing.get("setup_id") != setup_id:
            logger.error(f"❌ {symbol} is LOCKED by: {existing.get('setup_name')}")
            return False
        
        # Upsert: update if exists (same setup), insert if new
        lock_data = {
            "symbol": symbol,
            "setup_id": setup_id,
            "setup_name": setup_name,
            "locked_at": datetime.utcnow()
        }
        
        result = await collection.update_one(
            {"symbol": symbol},
            {"$set": lock_data},
            upsert=True
        )
        
        logger.info(f"✅ Lock acquired/refreshed for {symbol} by {setup_name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to acquire lock for {symbol}: {e}")
        return False


async def get_position_lock(db: AsyncIOMotorDatabase,
                           symbol: str) -> Optional[dict]:
    """
    ✅ NEW: Get lock information for an asset.
    
    Returns:
        Lock record or None if not locked
    """
    try:
        collection = db["position_locks"]
        lock = await collection.find_one({"symbol": symbol})
        return lock
        
    except Exception as e:
        logger.error(f"❌ Error getting lock: {e}")
        return None


async def release_position_lock(db: AsyncIOMotorDatabase,
                               symbol: str,
                               setup_id: str) -> bool:
    """
    ✅ NEW: Release lock when position is closed.
    
    Args:
        db: MongoDB database connection
        symbol: Asset symbol
        setup_id: Setup ID releasing lock
    
    Returns:
        True if lock released, False if error
    """
    try:
        logger.debug(f"release_position_lock called for symbol={symbol}, setup_id={setup_id}")
        collection = db["position_locks"]
        
        result = await collection.delete_one({
            "symbol": symbol,
            "setup_id": setup_id
        })
        logger.debug(f"release_position_lock deleted_count={result.deleted_count} for symbol={symbol}, setup_id={setup_id}")
        if result.deleted_count > 0:
            logger.info(f"✅ Released lock on {symbol}")
            return True
        else:
            logger.warning(f"⚠️ Lock not found for {symbol}")
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
        collection = db["position_locks"]
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
    result = await db.positions.insert_one(position_data)
    return str(result.inserted_id)
    
async def create_position_lock(symbol: str, setup_id: str) -> bool:
    """
    Shortcut compatible with reconciliation routine.
    """
    db = await get_db()
    # You can use the setup_id as setup_name, or fetch the name if needed
    return await acquire_position_lock(db, symbol, setup_id, setup_name=setup_id)

async def delete_position_lock(symbol: str = None) -> int:
    """
    If symbol is None, deletes ALL locks (startup cleanup).
    If symbol given, deletes single lock.
    """
    logger.debug(f"delete_position_lock called with symbol={symbol}")
    db = await get_db()
    collection = db["position_locks"]
    if symbol:
        result = await collection.delete_one({"symbol": symbol})
        logger.debug(f"delete_position_lock deleted_count={result.deleted_count} for symbol={symbol}")
        return result.deleted_count
    else:
        result = await collection.delete_many({})
        logger.debug(f"delete_position_lock deleted ALL locks, deleted_count={result.deleted_count}")
        return result.deleted_count
        
async def get_screener_positions_by_asset(asset: str):
    """Get all open screener positions for an asset."""
    db = await get_db()
    positions = await db.positions.find({
        "asset": asset,
        "status": "open",
        "source": "screener"
    }).to_list(None)
    return positions

async def create_screener_position_record(position_data: Dict[str, Any]):
    """Create a screener position record."""
    db = await get_db()
    result = await db.positions.insert_one(position_data)
    logger.info(f"✅ Screener position record created: {result.inserted_id}")
    return result.inserted_id

async def get_screener_indicator_cache(screener_setup_id: str, asset: str, indicator_name: str):
    """Get cached indicator for screener asset."""
    db = await get_db()
    cache = await db.screener_indicator_cache.find_one({
        "screener_setup_id": screener_setup_id,
        "asset": asset,
        "indicator_name": indicator_name
    })
    return cache

async def upsert_screener_indicator_cache(cache_data: Dict[str, Any]):
    """Update or insert screener indicator cache."""
    db = await get_db()
    await db.screener_indicator_cache.update_one(
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
        db = mongodb.get_db()
        balance = await db.paper_balances.find_one({"user_id": user_id})
        
        if not balance:
            # Create default paper balance
            default_balance = PaperBalance(user_id=user_id)
            result = await db.paper_balances.insert_one(
                default_balance.dict(by_alias=True, exclude={"id"})
            )
            balance = await db.paper_balances.find_one({"_id": result.inserted_id})
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
