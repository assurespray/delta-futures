# Add at top of main.py BEFORE any imports
from config.logging import configure_logging
configure_logging()

"""Main FastAPI application with Telegram webhook and Smart Algo Engine."""
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update
from config.settings import settings
from database.mongodb import mongodb
from bot import create_application
from services.algo_engine import AlgoEngine
from services.screener_engine import ScreenerEngine
from services.scheduler import scheduler_service
from services.logger_bot import LoggerBot  # ✅ Import CLASS (not instance!)
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
logger_bot = None  # ✅ Will be initialized in startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    global ptb_app, algo_engine, logger_bot
    
    # ========== STARTUP ==========
    logger.info("🚀 Starting application...")
    
    try:
        # ✅ FIRST: Initialize logger_bot (before any operations!)
        logger_bot = LoggerBot()
        logger.info("✅ Logger bot initialized")
        
        # Connect to MongoDB
        await mongodb.connect_db()
        logger.info("✅ MongoDB connected")
        
        # Setup position lock system
        logger.info("🔐 Setting up position lock system...")
        await mongodb.setup_position_lock_indexes()
        logger.info("✅ Position lock indexes created")
        
        # Clean stale locks (DO THIS FIRST!!)
        logger.info("🧹 Cleaning stale position locks...")
        from database.crud import cleanup_stale_locks
        db = mongodb.get_db()
        cleaned = await cleanup_stale_locks(db, max_age_minutes=60)
        if cleaned > 0:
            logger.warning(f"🧹 Cleaned {cleaned} stale position locks from previous session")
        else:
            logger.info("✅ No stale locks found")

        # ✅ NEW: Reconcile positions (AFTER MongoDB, BEFORE anything else!)
        from services.reconciliation import startup_reconciliation
        logger.info("🔍 Reconciling positions with exchange...")  # ← ADD THIS LINE
        await startup_reconciliation(logger_bot)
        logger.info("✅ Position reconciliation completed")      # ← ADD THIS LINE
        
        db = mongodb.get_db()
        cleaned = await cleanup_stale_locks(db, max_age_minutes=60)
        if cleaned > 0:
            logger.warning(f"🧹 Cleaned {cleaned} stale position locks from previous session")
        else:
            logger.info("✅ No stale locks found")
        
        # Validate setup configuration
        logger.info("🔍 Validating setup configuration...")
        await validate_setup_configuration()
        logger.info("✅ Setup configuration validated")
        
        # Create Telegram bot application
        ptb_app = create_application()
        
        # Set webhook
        webhook_url = settings.webhook_url
        logger.info(f"🔧 Setting webhook to: {webhook_url}")
        
        webhook_set = await ptb_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=False,
            allowed_updates=["message", "callback_query"]
        )
        
        if webhook_set:
            logger.info(f"✅ Webhook set successfully")
        else:
            logger.error("❌ Failed to set webhook!")
            raise RuntimeError("Webhook setup failed")
        
        # Verify webhook
        webhook_info = await ptb_app.bot.get_webhook_info()
        logger.info(f"📡 Webhook URL: {webhook_info.url}")
        logger.info(f"📊 Pending updates: {webhook_info.pending_update_count}")

        if webhook_info.last_error_date:
            logger.warning(f"⚠️ Last webhook error: {webhook_info.last_error_message}")
        
        # Initialize bot
        await ptb_app.initialize()
        await ptb_app.start()
        logger.info("✅ Telegram bot started")
        
        # Initialize scheduler
        scheduler_service.start()
        scheduler_service.add_cleanup_job()
        scheduler_service.add_health_check_job(health_check_task)
        logger.info("✅ Scheduler started")
        
        # ✅ Initialize algo engine with SMART monitoring
        algo_engine = AlgoEngine(logger_bot)
        logger.info("✅ Algo engine initialized")
        
        screener_engine = ScreenerEngine(logger_bot)
        logger.info("✅ Screener engine initialized")
        
        # Start algo monitoring (SMART boundary-aligned scheduling!)
        asyncio.create_task(algo_engine.run_continuous_monitoring())
        logger.info("✅ Algo monitoring started (SMART scheduling active)")

        # ----> Add this line right below to start the fill monitor:
        asyncio.create_task(algo_engine.monitor_pending_entries(poll_interval=5))
        logger.info("✅ Monitoring pending entry order")
        asyncio.create_task(screener_engine.run_continuous_monitoring())
        logger.info("✅ Monitoring screener pending entry order")

        # After starting algo/screener engines in lifespan()
        asyncio.create_task(run_order_reconciliation())
        logger.info("✅ Order reconciliation task started")

        # Send startup notification
        await logger_bot.send_info(
            "🚀 Trading Bot Started Successfully!\n\n"
            "✅ Features Active:\n"
            "  • Telegram Bot\n"
            "  • Smart Algo Engine\n"
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
        # Release position locks
        #logger.info("🔐 Releasing all position locks...")
        #db = mongodb.get_db()
        #collection = db["position_locks"]
        #result = await collection.delete_many({})
        
        #if result.deleted_count > 0:
            #logger.info(f"✅ Released {result.deleted_count} position locks")
        
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
        
        # Send shutdown notification
        if logger_bot:
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


async def run_order_reconciliation():
    from services.order_reconciler import reconcile_pending_orders
    while True:
        await reconcile_pending_orders(logger_bot)
        await asyncio.sleep(60)  # Check every 60 seconds; adjust as needed


async def validate_setup_configuration():
    """Validate no asset has multiple active setups."""
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
                logger.warning(f"⚠️ CONFLICT: {symbol} has {len(setups)} active setups!")
                for setup in setups:
                    logger.warning(f"   - {setup['setup_name']} ({setup.get('timeframe', 'N/A')})")
                conflicts = True
        
        if conflicts:
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
    description="Automated futures trading with Smart Algo Engine + Asset Lock Protection",
    version="2.0.0",
    lifespan=lifespan
)


@app.post("/")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram webhook updates."""
    try:
        req = await request.json()
        update = Update.de_json(req, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=200)
    
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        return Response(status_code=500)


@app.get("/")
@app.head("/")
async def root():
    """Root endpoint with health status."""
    from datetime import datetime
    
    return {
        "message": "Delta Exchange Trading Bot API",
        "status": "running",
        "version": "2.0.0",
        "engine": "Smart Boundary-Aligned",
        "features": [
            "Dual SuperTrend Strategy",
            "Stop-Loss Protection",
            "Asset Lock System",
            "Multi-Setup Safety",
            "Telegram Bot Control"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }


@app.head("/universal/health")
@app.head("/health")
async def health_check_head():
    """Health check for HEAD requests."""
    return Response(status_code=200)


@app.get("/universal/health")
@app.get("/health")
async def health_check_get():
    """Health check for GET requests with detailed metrics."""
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
            "engine": "Smart Boundary-Aligned",
            "features": {
                "asset_lock": "enabled",
                "stop_loss_protection": "enabled",
                "multi_setup_safety": "enabled",
                "smart_scheduling": "enabled"
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
            if logger_bot:  # ✅ Check if initialized
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
    
