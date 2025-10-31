"""CRUD operations for database collections."""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
from database.mongodb import mongodb
from database.models import APICredential, AlgoSetup, AlgoActivity, IndicatorCache
from cryptography.fernet import Fernet
from config.settings import settings

logger = logging.getLogger(__name__)

# Initialize Fernet cipher for encryption
cipher_suite = Fernet(settings.encryption_key.encode())


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
    """Delete API credential."""
    try:
        result = await mongodb.get_db().api_credentials.delete_one({
            "_id": ObjectId(credential_id),
            "user_id": user_id
        })
        
        if result.deleted_count > 0:
            logger.info(f"✅ API credential deleted: {credential_id}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ Failed to delete API credential: {e}")
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
    """Delete algo setup."""
    try:
        result = await mongodb.get_db().algo_setups.delete_one({
            "_id": ObjectId(setup_id),
            "user_id": user_id
        })
        
        if result.deleted_count > 0:
            logger.info(f"✅ Algo setup deleted: {setup_id}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ Failed to delete algo setup: {e}")
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


# ==================== Algo Activity CRUD ====================

async def create_algo_activity(activity_data: Dict[str, Any]) -> str:
    """Create new algo activity record."""
    try:
        activity = AlgoActivity(**activity_data)
        result = await mongodb.get_db().algo_activity.insert_one(activity.dict(by_alias=True, exclude={"id"}))
        logger.info(f"✅ Algo activity created for setup: {activity.algo_setup_name}")
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"❌ Failed to create algo activity: {e}")
        raise


async def update_algo_activity(activity_id: str, update_data: Dict[str, Any]) -> bool:
    """Update algo activity (usually for exit data)."""
    try:
        result = await mongodb.get_db().algo_activity.update_one(
            {"_id": ObjectId(activity_id)},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"✅ Algo activity updated: {activity_id}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ Failed to update algo activity: {e}")
        return False


async def get_algo_activity_by_user(user_id: str, days: int = 3) -> List[Dict[str, Any]]:
    """Get algo activity for a user for the last N days."""
    try:
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        cursor = mongodb.get_db().algo_activity.find({
            "user_id": user_id,
            "trade_date": {"$gte": cutoff_date}
        }).sort("entry_time", -1)
        
        activities = await cursor.to_list(length=1000)
        
        for activity in activities:
            activity["_id"] = str(activity["_id"])
        
        return activities
        
    except Exception as e:
        logger.error(f"❌ Failed to get algo activity: {e}")
        return []


async def get_open_activity_by_setup(algo_setup_id: str) -> Optional[Dict[str, Any]]:
    """Get open (unclosed) activity for an algo setup."""
    try:
        activity = await mongodb.get_db().algo_activity.find_one({
            "algo_setup_id": algo_setup_id,
            "is_closed": False
        })
        
        if activity:
            activity["_id"] = str(activity["_id"])
        
        return activity
        
    except Exception as e:
        logger.error(f"❌ Failed to get open activity: {e}")
        return None


async def cleanup_old_activities():
    """Delete activities older than retention period."""
    try:
        cutoff_date = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
        
        result = await mongodb.get_db().algo_activity.delete_many({
            "trade_date": {"$lt": cutoff_date}
        })
        
        if result.deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {result.deleted_count} old activity records")
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup old activities: {e}")


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
    """Delete screener setup."""
    try:
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
        
      
