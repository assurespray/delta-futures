"""Main FastAPI application with Telegram webhook."""
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
    
    Args:
        app: FastAPI application
    """
    global ptb_app, algo_engine
    
    # Startup
    logger.info("🚀 Starting application...")
    
    try:
        # Connect to MongoDB
        await mongodb.connect_db()
        
        # Create Telegram bot application
        ptb_app = create_application()
        
        # Set webhook
        webhook_url = settings.webhook_url
        await ptb_app.bot.set_webhook(webhook_url)
        logger.info(f"✅ Webhook set to: {webhook_url}")
        
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
        
        await logger_bot.send_info("🚀 Trading Bot Started Successfully!")
        
        yield
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise
    
    # Shutdown
    logger.info("🔒 Shutting down application...")
    
    try:
        # Stop scheduler
        scheduler_service.shutdown()
        
        # Stop bot
        if ptb_app:
            await ptb_app.stop()
            await ptb_app.shutdown()
        
        # Close MongoDB
        await mongodb.close_db()
        
        await logger_bot.send_warning("🔒 Trading Bot Shut Down")
        
        logger.info("✅ Shutdown complete")
        
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")


# Create FastAPI app
app = FastAPI(
    title="Delta Exchange Trading Bot",
    description="Automated futures trading with Telegram bot interface",
    version="1.0.0",
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


@app.head("/universal/health")
async def health_check_head():
    """
    Health check endpoint for HEAD requests (UptimeRobot).
    
    Returns:
        Response with 200 status
    """
    return Response(status_code=200)


@app.get("/universal/health")
async def health_check_get():
    """
    Health check endpoint for GET requests.
    
    Returns:
        JSON with health status
    """
    from datetime import datetime
    from database.crud import get_all_active_algo_setups
    
    try:
        active_setups = await get_all_active_algo_setups()
        active_count = len(active_setups) if active_setups else 0
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "active_algos": active_count,
            "scheduler_jobs": scheduler_service.get_job_count(),
            "environment": settings.environment
        }
    
    except Exception as e:
        logger.error(f"❌ Health check error: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


@app.get("/")
async def root():
    """
    Root endpoint.
    
    Returns:
        Welcome message
    """
    return {
        "message": "Delta Exchange Trading Bot API",
        "status": "running",
        "version": "1.0.0"
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
  
