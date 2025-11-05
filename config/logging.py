"""Production logging configuration - minimal verbose output"""
import logging
import sys

def configure_logging():
    """
    Configure logging for production:
    - ONLY errors and critical logs for external libs
    - INFO level for our app
    - Silent for verbose libraries
    """
    
    # Root logger - INFO level
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # ✅ SILENCE verbose external libs
    logging.getLogger("httpx").setLevel(logging.WARNING)         # ❌ Hide HTTP requests
    logging.getLogger("httpcore").setLevel(logging.WARNING)      # ❌ Hide HTTP core
    logging.getLogger("telegram").setLevel(logging.WARNING)      # ❌ Hide Telegram spam
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)  # ❌ Hide Telegram ext
    logging.getLogger("apscheduler").setLevel(logging.WARNING)   # ❌ Hide scheduler
    logging.getLogger("motor").setLevel(logging.WARNING)         # ❌ Hide Motor debug
    logging.getLogger("pymongo").setLevel(logging.WARNING)       # ❌ Hide PyMongo
    
    # ✅ KEEP our app logs
    logging.getLogger("main").setLevel(logging.INFO)
    logging.getLogger("services").setLevel(logging.INFO)
    logging.getLogger("database").setLevel(logging.INFO)
    logging.getLogger("api").setLevel(logging.INFO)
    logging.getLogger("strategy").setLevel(logging.INFO)
    
    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    
    # Simple format - no timestamp noise
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    # Remove existing handlers and add ours
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
  
