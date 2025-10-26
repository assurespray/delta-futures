"""Self-ping mechanism for health monitoring."""
import logging
import asyncio
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)


class SelfPing:
    """Self-ping service to keep app alive and monitor health."""
    
    def __init__(self):
        """Initialize self-ping service."""
        self.ping_url = f"{settings.webhook_url}/universal/health"
        self.fail_count = 0
        self.max_fails = 3
    
    async def ping(self) -> bool:
        """
        Perform health check ping.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.head(self.ping_url)
                
                if response.status_code == 200:
                    logger.info("✅ Self-ping successful")
                    self.fail_count = 0
                    return True
                else:
                    logger.warning(f"⚠️ Self-ping returned status {response.status_code}")
                    self.fail_count += 1
                    return False
        
        except Exception as e:
            logger.error(f"❌ Self-ping failed: {e}")
            self.fail_count += 1
            return False
    
    def is_critical(self) -> bool:
        """
        Check if failure count has reached critical threshold.
        
        Returns:
            True if critical, False otherwise
        """
        return self.fail_count >= self.max_fails
    
    def reset_fail_count(self):
        """Reset failure counter."""
        self.fail_count = 0


# Global self-ping instance
self_ping = SelfPing()
