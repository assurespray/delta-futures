import logging
from typing import List
from datetime import datetime, timedelta
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


async def cleanup_old_order_records(max_age_days: int = 7):
    """
    Delete order records older than max_age_days.
    Keeps the database lean on free-tier MongoDB.
    """
    try:
        db = mongodb.get_db()
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        
        result = await db.orders.delete_many({
            "submitted_at": {"$lt": cutoff}
        })
        
        if result.deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {result.deleted_count} order records older than {max_age_days} days")
        else:
            logger.info(f"✅ No old order records to clean up")
        
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup old order records: {e}")
        return 0


async def cleanup_closed_positions(max_age_days: int = 7):
    """
    Delete closed position records older than max_age_days.
    """
    try:
        db = mongodb.get_db()
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        
        result = await db.positions.delete_many({
            "status": "closed",
            "closed_at": {"$lt": cutoff}
        })
        
        if result.deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {result.deleted_count} closed position records older than {max_age_days} days")
        else:
            logger.info(f"✅ No old closed position records to clean up")
        
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup closed positions: {e}")
        return 0


async def cleanup_closed_activities(max_age_days: int = 7):
    """
    Delete closed algo activity records older than max_age_days.
    Preserves open (unclosed) activities regardless of age.
    """
    try:
        db = mongodb.get_db()
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        
        result = await db.trade_states.delete_many({
            "status": {"$in": ["closed", "cancelled"]},
            "exit_time": {"$lt": cutoff}
        })
        
        if result.deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {result.deleted_count} closed activity records older than {max_age_days} days")
        else:
            logger.info(f"✅ No old closed activity records to clean up")
        
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup closed activities: {e}")
        return 0


async def run_full_cleanup(max_age_days: int = 7):
    """
    Run all cleanup tasks. Call on bot startup or periodically.
    Optimized for free-tier MongoDB storage limits.
    
    Args:
        max_age_days: Delete records older than this many days (default 7)
    
    Returns:
        Dict with cleanup summary
    """
    logger.info(f"🧹 Starting full database cleanup (max_age={max_age_days} days)...")
    
    results = {
        "indicator_cache": await cleanup_stale_indicator_cache(),
        "old_orders": await cleanup_old_order_records(max_age_days),
        "closed_positions": await cleanup_closed_positions(max_age_days),
        "closed_activities": await cleanup_closed_activities(max_age_days),
    }
    
    total_deleted = (
        (results["indicator_cache"].get("deleted", 0) if isinstance(results["indicator_cache"], dict) else 0) +
        results["old_orders"] +
        results["closed_positions"] +
        results["closed_activities"]
    )
    
    logger.info(
        f"🧹 Full cleanup completed: {total_deleted} total records removed\n"
        f"   Indicator cache: {results['indicator_cache']}\n"
        f"   Old orders: {results['old_orders']}\n"
        f"   Closed positions: {results['closed_positions']}\n"
        f"   Closed activities: {results['closed_activities']}"
    )
    
    return results


async def get_indicator_cache_stats():
    """Get statistics about current indicator cache."""
    try:
        db = mongodb.get_db()
        
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


async def get_db_storage_stats():
    """Get storage usage stats for all collections (useful for free-tier monitoring)."""
    try:
        db = mongodb.get_db()
        
        collections = ["orders", "positions", "trade_states", "indicator_cache",
                       "algo_setups", "position_locks", "api_credentials"]
        
        stats = {}
        for coll_name in collections:
            try:
                count = await db[coll_name].count_documents({})
                stats[coll_name] = count
            except Exception:
                stats[coll_name] = "error"
        
        logger.info(f"📊 DB Storage Stats: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"❌ Failed to get DB storage stats: {e}")
        return None
      
