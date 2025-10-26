"""APScheduler service for timeframe-based job scheduling."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import pytz
from database.crud import cleanup_old_activities

logger = logging.getLogger(__name__)


class SchedulerService:
    """Scheduler for managing periodic tasks."""
    
    def __init__(self):
        """Initialize scheduler."""
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.jobs = {}
    
    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("✅ Scheduler started")
    
    def shutdown(self):
        """Shutdown the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("🔒 Scheduler stopped")
    
    def add_cleanup_job(self):
        """Add daily cleanup job for old activity records."""
        try:
            # Run daily at 00:00 UTC
            self.scheduler.add_job(
                func=cleanup_old_activities,
                trigger=CronTrigger(hour=0, minute=0),
                id="cleanup_old_activities",
                name="Daily Activity Cleanup",
                replace_existing=True
            )
            logger.info("✅ Daily cleanup job scheduled")
            
        except Exception as e:
            logger.error(f"❌ Failed to add cleanup job: {e}")
    
    def add_health_check_job(self, health_check_func, interval_seconds: int = 300):
        """
        Add periodic health check job.
        
        Args:
            health_check_func: Async function to call for health check
            interval_seconds: Interval in seconds (default 5 minutes)
        """
        try:
            self.scheduler.add_job(
                func=health_check_func,
                trigger=IntervalTrigger(seconds=interval_seconds),
                id="health_check",
                name="Self Health Check",
                replace_existing=True
            )
            logger.info(f"✅ Health check job scheduled (every {interval_seconds}s)")
            
        except Exception as e:
            logger.error(f"❌ Failed to add health check job: {e}")
    
    def get_job_count(self) -> int:
        """Get number of scheduled jobs."""
        return len(self.scheduler.get_jobs())


# Global scheduler instance
scheduler_service = SchedulerService()
