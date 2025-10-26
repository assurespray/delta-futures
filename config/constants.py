"""Trading constants and application-wide constants."""

# Timeframe mappings for Delta Exchange API
TIMEFRAME_MAPPING = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d"
}

# Timeframe in seconds for scheduler
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400
}

# SuperTrend indicator parameters
PERUSU_ATR_LENGTH = 20
PERUSU_FACTOR = 20
SIRUSU_ATR_LENGTH = 10
SIRUSU_FACTOR = 10

# Trading direction options
DIRECTION_BOTH = "both"
DIRECTION_LONG_ONLY = "long_only"
DIRECTION_SHORT_ONLY = "short_only"

# Order types
ORDER_TYPE_MARKET = "market_order"
ORDER_TYPE_LIMIT = "limit_order"

# Order sides
ORDER_SIDE_BUY = "buy"
ORDER_SIDE_SELL = "sell"

# SuperTrend signals
SIGNAL_UPTREND = 1
SIGNAL_DOWNTREND = -1

# API rate limiting
MAX_REQUESTS_PER_SECOND = 10
REQUEST_RETRY_ATTEMPTS = 3
REQUEST_RETRY_DELAY = 2  # seconds

# Data retention
ALGO_ACTIVITY_RETENTION_DAYS = 3

# Logging
LOG_FILE_MAX_SIZE = 100 * 1024 * 1024  # 100MB
LOG_FILE_BACKUP_COUNT = 5

# Health check
SELF_PING_INTERVAL = 300  # 5 minutes
SELF_PING_FAIL_THRESHOLD = 3

# Cache expiry
PRODUCT_CACHE_EXPIRY = 86400  # 24 hours
