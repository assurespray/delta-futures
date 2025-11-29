import logging
from typing import List
from database.mongodb import mongodb

logger = logging.getLogger(__name__)

async def cleanup_stale_indicator_cache():
    """
    Remove indicator_cache entries for inactive/deleted algo setups.
    Should be called on bot startup.
    """
    try:
        db = mongodb.get_db()
        
        # 1. Get all active algo setup IDs
        active_algo_setups = await db.algo_setups.find(
            {"asset": {"$ne": "MANUAL"}},  # Exclude manual/inactive
            {"_id": 1}
        ).to_list(None)
        
        active_ids = [str(setup["_id"]) for setup in active_algo_setups]
        
        logger.info(f"🔍 Found {len(active_ids)} active algo setups")
        
        # 2. Get all active screener setup IDs (if you have screeners)
        # active_screener_setups = await db.screener_setups.find(
        #     {"active": True},
        #     {"_id": 1}
        # ).to_list(None)
        # active_ids.extend([str(s["_id"]) for s in active_screener_setups])
        
        # 3. Find stale indicator cache entries
        stale_entries = await db.indicator_cache.count_documents({
            "algo_setup_id": {"$nin": active_ids}
        })
        
        if stale_entries == 0:
            logger.info("✅ No stale indicator cache entries found")
            return {"deleted": 0, "active_setups": len(active_ids)}
        
        # 4. Delete stale entries
        result = await db.indicator_cache.delete_many({
            "algo_setup_id": {"$nin": active_ids}
        })
        
        deleted_count = result.deleted_count
        
        logger.info(f"🗑️ Cleaned up {deleted_count} stale indicator cache entries")
        logger.info(f"✅ Kept cache for {len(active_ids)} active setups")
        
        return {
            "deleted": deleted_count,
            "active_setups": len(active_ids)
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup indicator cache: {e}")
        return {"deleted": 0, "active_setups": 0, "error": str(e)}


async def get_indicator_cache_stats():
    """Get statistics about current indicator cache."""
    try:
        db = get_database()
        
        total = await db.indicator_cache.count_documents({})
        
        # Group by algo_setup_id
        pipeline = [
            {"$group": {
                "_id": "$algo_setup_id",
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}}
        ]
        
        by_setup = await db.indicator_cache.aggregate(pipeline).to_list(None)
        
        logger.info(f"📊 Indicator cache stats: {total} total entries across {len(by_setup)} setups")
        
        return {
            "total_entries": total,
            "unique_setups": len(by_setup),
            "by_setup": by_setup
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to get cache stats: {e}")
        return None
      
