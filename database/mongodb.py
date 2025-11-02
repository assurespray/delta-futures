"""MongoDB connection and database initialization."""
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config.settings import settings

logger = logging.getLogger(__name__)


class MongoDB:
    """MongoDB connection manager."""
    
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
            
            logger.info("✅ Database indexes created successfully")
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to create indexes: {e}")

    async def setup_position_lock_indexes(db):
        """Set up unique index for position locks."""
        try:
            collection = db["position_locks"]
        
            # Create unique index on symbol (only one lock per symbol)
            await collection.create_index(
                "symbol",
                unique=True,
                sparse=True  # Allow multiple null values
            )
        
            logger.info("✅ Position lock indexes created")
        
        except Exception as e:
            logger.error(f"❌ Error creating indexes: {e}")
    
    @classmethod
    def get_db(cls):
        """Get database instance."""
        if cls.db is None:
            raise Exception("Database not connected. Call connect_db() first.")
        return cls.db


# Singleton instance
mongodb = MongoDB()
