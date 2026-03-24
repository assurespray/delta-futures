"""MongoDB connection and database initialization with asset lock support."""
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config.settings import settings

logger = logging.getLogger(__name__)

# TTL expiry in seconds (7 days)
TTL_EXPIRY_SECONDS = 7 * 24 * 60 * 60


class MongoDB:
    """MongoDB connection manager with asset lock support."""
    
    client: AsyncIOMotorClient = None
    db = None
    
    @classmethod
    async def connect_db(cls):
        """Connect to MongoDB."""
        try:
            cls.client = AsyncIOMotorClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=5000,
                maxPoolSize=10,
                minPoolSize=1
            )
            cls.db = cls.client[settings.mongodb_db_name]
            
            # Test connection
            await cls.client.admin.command('ping')
            logger.info(f"✅ Connected to MongoDB: {settings.mongodb_db_name}")
            
            # Create indexes
            await cls.create_indexes()
            
            # Setup position lock indexes
            await cls.setup_position_lock_indexes()
            
            # Setup TTL indexes for automatic cleanup (free-tier optimization)
            await cls.setup_ttl_indexes()
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise
    
    @classmethod
    async def close_db(cls):
        """Close MongoDB connection."""
        if cls.client:
            cls.client.close()
            logger.info("🔒 MongoDB connection closed")
    
    @classmethod
    async def create_indexes(cls):
        """Create database indexes for optimal query performance."""
        try:
            # API Credentials indexes
            await cls.db.api_credentials.create_index("user_id")
            await cls.db.api_credentials.create_index([("user_id", 1), ("api_name", 1)])
            
            # Algo Setups indexes
            await cls.db.algo_setups.create_index("user_id")
            await cls.db.algo_setups.create_index("api_id")
            await cls.db.algo_setups.create_index([("user_id", 1), ("is_active", 1)])
            
            # Algo Activity indexes
            await cls.db.algo_activity.create_index("user_id")
            await cls.db.algo_activity.create_index("algo_setup_id")
            await cls.db.algo_activity.create_index("trade_date")
            await cls.db.algo_activity.create_index([("user_id", 1), ("trade_date", -1)])
            
            # Indicator Cache indexes
            await cls.db.indicator_cache.create_index("algo_setup_id")
            await cls.db.indicator_cache.create_index([("asset", 1), ("timeframe", 1)])
            
            # Order records indexes
            await cls.db.orders.create_index("order_id")
            await cls.db.orders.create_index("algo_setup_id")
            
            logger.info("✅ Database indexes created successfully")
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to create indexes: {e}")

    @classmethod
    async def setup_position_lock_indexes(cls):
        """Set up unique index for position locks."""
        try:
            collection = cls.db["position_locks"]
        
            # Create unique index on symbol (only one lock per symbol)
            await collection.create_index(
                "symbol",
                unique=True,
                sparse=True  # Allow multiple null values
            )
        
            logger.info("✅ Position lock indexes created")
        
        except Exception as e:
            logger.error(f"❌ Error creating position lock indexes: {e}")

    @classmethod
    async def setup_ttl_indexes(cls):
        """
        Set up TTL (Time-To-Live) indexes for automatic document expiry.
        MongoDB automatically deletes documents after the specified time.
        This keeps the free-tier storage usage under control.
        
        Note: TTL indexes run a background task every 60 seconds to remove
        expired documents. The actual deletion may lag by up to 60 seconds.
        
        If a TTL index already exists with a different expireAfterSeconds,
        we drop and recreate it.
        """
        try:
            # Orders: auto-delete after 7 days based on submitted_at
            await cls._ensure_ttl_index(
                cls.db.orders, "submitted_at", TTL_EXPIRY_SECONDS, "orders"
            )
            
            # Closed positions: auto-delete after 7 days based on closed_at
            # Only closed positions have closed_at set, so open positions are safe
            await cls._ensure_ttl_index(
                cls.db.positions, "closed_at", TTL_EXPIRY_SECONDS, "positions"
            )
            
            logger.info(f"✅ TTL indexes configured (expiry: {TTL_EXPIRY_SECONDS // 86400} days)")
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to setup TTL indexes: {e}")

    @classmethod
    async def _ensure_ttl_index(cls, collection, field: str, expire_seconds: int, name: str):
        """
        Create or update a TTL index on a collection.
        If the index already exists with a different TTL, drop and recreate it.
        """
        index_name = f"{field}_ttl"
        try:
            # Check existing indexes
            existing_indexes = await collection.index_information()
            
            if index_name in existing_indexes:
                existing_ttl = existing_indexes[index_name].get("expireAfterSeconds")
                if existing_ttl == expire_seconds:
                    logger.debug(f"TTL index on {name}.{field} already correct ({expire_seconds}s)")
                    return
                else:
                    # Drop and recreate with new TTL
                    logger.info(f"🔄 Updating TTL index on {name}.{field}: {existing_ttl}s -> {expire_seconds}s")
                    await collection.drop_index(index_name)
            
            await collection.create_index(
                field,
                expireAfterSeconds=expire_seconds,
                name=index_name,
                sparse=True  # Only index documents that have this field
            )
            logger.info(f"✅ TTL index created on {name}.{field} (expires after {expire_seconds}s)")
            
        except Exception as e:
            logger.warning(f"⚠️ Could not set TTL index on {name}.{field}: {e}")
    
    @classmethod
    def get_db(cls):
        """Get database instance."""
        if cls.db is None:
            raise Exception("Database not connected. Call connect_db() first.")
        return cls.db


# Singleton instance
mongodb = MongoDB()
        
