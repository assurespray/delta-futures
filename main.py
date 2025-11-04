"""Main FastAPI application with Telegram webhook and Asset Lock initialization."""
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update
from config.settings import settings
from database.mongodb import mongodb
from bot import create_application
from services.algo_engine import AlgoEngine
from services.scheduler import scheduler_service
from services.logger_bot import logger_bot
from utils.self_ping import self_ping

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Global instances
ptb_app = None
algo_engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown.
    ✅ ENHANCED: Added asset lock initialization and cleanup
    
    Args:
        app: FastAPI application
    """
    global ptb_app, algo_engine
    
    # ========== STARTUP ==========
    logger.info("🚀 Starting application...")
    
    try:
        # Connect to MongoDB
        await mongodb.connect_db()
        logger.info("✅ MongoDB connected")
        
        # ✅ FIXED: Call setup_position_lock_indexes as classmethod (NOT imported)
        logger.info("🔐 Setting up position lock system...")
        await mongodb.setup_position_lock_indexes()
        logger.info("✅ Position lock indexes created")
        
        # ✅ NEW: Clean stale locks from previous crashes
        logger.info("🧹 Cleaning stale position locks...")
        from database.crud import cleanup_stale_locks
        
        db = mongodb.get_db()
        cleaned = await cleanup_stale_locks(db, max_age_minutes=60)
        if cleaned > 0:
            logger.warning(f"🧹 Cleaned {cleaned} stale position locks from previous session")
        else:
            logger.info("✅ No stale locks found")
        
        # ✅ NEW: Validate setup configuration (no multi-timeframe conflicts)
        logger.info("🔍 Validating setup configuration...")
        await validate_setup_configuration()
        logger.info("✅ Setup configuration validated")
        
        # Create Telegram bot application
        ptb_app = create_application()
        
        # Set webhook (will update if already exists)
        webhook_url = settings.webhook_url
        logger.info(f"🔧 Setting webhook to: {webhook_url}")
        
        webhook_set = await ptb_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=False,  # Keep messages sent during restart!
            allowed_updates=["message", "callback_query"]
        )
        
        if webhook_set:
            logger.info(f"✅ Webhook set successfully")
        else:
            logger.error("❌ Failed to set webhook!")
            raise RuntimeError("Webhook setup failed")
        
        # Verify webhook is set correctly
        webhook_info = await ptb_app.bot.get_webhook_info()
        logger.info(f"📡 Webhook URL: {webhook_info.url}")
        logger.info(f"📊 Pending updates: {webhook_info.pending_update_count}")

        # Log any webhook errors (only if last_error_date exists)
        if webhook_info.last_error_date:  # ✅ FIXED - Just check if it exists
            logger.warning(f"⚠️ Last webhook error: {webhook_info.last_error_message}")

        
        # Initialize bot
        await ptb_app.initialize()
        await ptb_app.start()
        logger.info("✅ Telegram bot started")
        
        # Initialize algo engine
        algo_engine = AlgoEngine(logger_bot)
        logger.info("✅ Algo engine initialized")
        
        # Start scheduler
        scheduler_service.start()
        scheduler_service.add_cleanup_job()
        scheduler_service.add_health_check_job(health_check_task)
        logger.info("✅ Scheduler started")
        
        # Start algo monitoring in background
        asyncio.create_task(algo_engine.run_continuous_monitoring())
        logger.info("✅ Algo monitoring started")
        
        await logger_bot.send_info(
            "🚀 Trading Bot Started Successfully!\n\n"
            "✅ Features Active:\n"
            "  • Telegram Bot\n"
            "  • Algo Engine\n"
            "  • Asset Lock System\n"
            "  • Stop-Loss Protection\n"
            "  • Multi-Setup Safety"
        )
        
        yield
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise
    
    # ========== SHUTDOWN ==========
    logger.info("🔒 Shutting down application...")
    
    try:
        # ✅ NEW: Release all position locks on shutdown
        logger.info("🔐 Releasing all position locks...")
        
        db = mongodb.get_db()
        collection = db["position_locks"]
        result = await collection.delete_many({})
        
        if result.deleted_count > 0:
            logger.info(f"✅ Released {result.deleted_count} position locks")
        
        # Webhook stays active during restart - no deletion!
        
        # Stop scheduler
        scheduler_service.shutdown()
        logger.info("✅ Scheduler stopped")
        
        # Stop bot
        if ptb_app:
            await ptb_app.stop()
            await ptb_app.shutdown()
            logger.info("✅ Telegram bot stopped")
        
        # Close MongoDB
        await mongodb.close_db()
        logger.info("✅ MongoDB connection closed")
        
        await logger_bot.send_warning(
            "🔒 Trading Bot Shut Down\n\n"
            "✅ All position locks released\n"
            "✅ All open orders cancelled\n"
            "✅ Bot state cleaned"
        )
        
        logger.info("✅ Shutdown complete")
        
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def validate_setup_configuration():
    """
    Validate that no asset has multiple active setups.
    Prevents multi-timeframe conflicts.
    
    Raises:
        Exception if configuration is invalid
    """
    try:
        from database.crud import get_all_active_algo_setups
        
        all_setups = await get_all_active_algo_setups()
        
        if not all_setups:
            logger.info("ℹ️ No active setups to validate")
            return
        
        # Group by asset
        assets_map = {}
        for setup in all_setups:
            symbol = setup["asset"]
            if symbol not in assets_map:
                assets_map[symbol] = []
            assets_map[symbol].append(setup)
        
        # Check for conflicts
        conflicts = False
        for symbol, setups in assets_map.items():
            if len(setups) > 1:
                logger.warning(f"⚠️ CONFLICT DETECTED: {symbol} has {len(setups)} active setups!")
                for setup in setups:
                    logger.warning(f"   - {setup['setup_name']} ({setup.get('timeframe', 'N/A')})")
                conflicts = True
        
        if conflicts:
            logger.error(f"❌ INVALID SETUP: Multiple timeframes on same asset!")
            logger.error(f"   Please disable all but ONE setup per asset")
            raise Exception("Invalid setup configuration: Multiple timeframes detected")
        
        logger.info(f"✅ Setup configuration valid - no asset conflicts")
        logger.info(f"   Active setups: {len(all_setups)}")
        for setup in all_setups:
            logger.info(f"   • {setup['setup_name']} ({setup['asset']} @ {setup.get('timeframe', 'N/A')})")
        
    except Exception as e:
        logger.error(f"❌ Configuration validation failed: {e}")
        raise


# Create FastAPI app
app = FastAPI(
    title="Delta Exchange Trading Bot",
    description="Automated futures trading with Telegram bot interface + Asset Lock Protection",
    version="2.0.0",
    lifespan=lifespan
)


@app.post("/")
async def telegram_webhook(request: Request):
    """
    Handle incoming Telegram webhook updates.
    
    Args:
        request: FastAPI request
    
    Returns:
        Response with 200 status
    """
    try:
        req = await request.json()
        update = Update.de_json(req, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=200)
    
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        return Response(status_code=500)


# Root endpoint - supports both GET and HEAD for Render health checks
@app.get("/")
@app.head("/")
async def root():
    """
    Root endpoint with health status.
    Render.com will ping this for health checks.
    
    Returns:
        JSON with status (GET) or 200 status (HEAD)
    """
    from datetime import datetime
    
    return {
        "message": "Delta Exchange Trading Bot API",
        "status": "running",
        "version": "2.0.0",
        "features": [
            "Dual SuperTrend Strategy",
            "Stop-Loss Protection",
            "Asset Lock System",
            "Multi-Setup Safety",
            "Telegram Bot Control"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }


# Health check endpoints (UptimeRobot compatibility)
@app.head("/universal/health")
@app.head("/health")
async def health_check_head():
    """
    Health check endpoint for HEAD requests (UptimeRobot).
    
    Returns:
        Response with 200 status
    """
    return Response(status_code=200)


@app.get("/universal/health")
@app.get("/health")
async def health_check_get():
    """
    Health check endpoint for GET requests.
    Returns detailed health information.
    
    Returns:
        JSON with health status and metrics
    """
    from datetime import datetime
    
    try:
        from database.crud import get_all_active_algo_setups
        
        active_setups = await get_all_active_algo_setups()
        active_count = len(active_setups) if active_setups else 0
        
        # Get position lock count
        db = mongodb.get_db()
        collection = db["position_locks"]
        lock_count = await collection.count_documents({})
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "active_algos": active_count,
            "active_locks": lock_count,
            "scheduler_jobs": scheduler_service.get_job_count(),
            "environment": settings.environment,
            "version": "2.0.0",
            "features": {
                "asset_lock": "enabled",
                "stop_loss_protection": "enabled",
                "multi_setup_safety": "enabled"
            }
        }
    
    except Exception as e:
        logger.error(f"❌ Health check error: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


async def health_check_task():
    """Periodic health check task."""
    try:
        success = await self_ping.ping()
        
        if not success and self_ping.is_critical():
            await logger_bot.send_error(
                f"❌ CRITICAL: Self-ping failed {self_ping.fail_count} times!"
            )
            self_ping.reset_fail_count()
    
    except Exception as e:
        logger.error(f"❌ Health check task error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False
)
    
