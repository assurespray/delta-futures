"""Trading constants and application-wide constants."""

# ✅ COMPLETE: Timeframe mappings for Delta Exchange API (ALL TIMEFRAMES)
TIMEFRAME_MAPPING = {
    # ===== MINUTES =====
    "1m": "1m",
    "2m": "3m",       # ← Delta doesn't have 2m, use 3m
    "3m": "3m",       # ← CRITICAL: WAS MISSING!
    "4m": "5m",       # ← Delta doesn't have 4m, use 5m
    "5m": "5m",
    "10m": "15m",     # ← Delta doesn't have 10m, use 15m
    "15m": "15m",
    "20m": "30m",     # ← Delta doesn't have 20m, use 30m
    "30m": "30m",
    "45m": "1h",      # ← Delta doesn't have 45m, use 1h
    "1h": "1h",
    
    # ===== HOURS =====
    "2h": "4h",       # ← Delta doesn't have 2h, use 4h
    "3h": "4h",       # ← Delta doesn't have 3h, use 4h
    "4h": "4h",
    "6h": "1d",       # ← Delta doesn't have 6h, use 1d
    "8h": "1d",       # ← Delta doesn't have 8h, use 1d
    "12h": "1d",      # ← Delta doesn't have 12h, use 1d
    
    # ===== DAYS =====
    "1d": "1d",
    "2d": "1d",       # ← Delta doesn't have 2d, use 1d
    "3d": "1d",       # ← Delta doesn't have 3d, use 1d
    "7d": "1d",       # ← Delta doesn't have 7d, use 1d
    "1w": "1d",       # ← Delta doesn't have 1w, use 1d
}

# Time in force
TIME_IN_FORCE_GTC = "gtc"
TIME_IN_FORCE_IOC = "ioc"
TIME_IN_FORCE_FOK = "fok"

# ✅ COMPLETE: Timeframe in seconds for scheduler (ALL TIMEFRAMES)
TIMEFRAME_SECONDS = {
    # ===== MINUTES =====
    "1m": 60,
    "2m": 120,
    "3m": 180,        # ← CRITICAL: WAS MISSING!
    "4m": 240,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "20m": 1200,
    "30m": 1800,
    "45m": 2700,
    "1h": 3600,
    
    # ===== HOURS =====
    "2h": 7200,
    "3h": 10800,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    
    # ===== DAYS =====
    "1d": 86400,
    "2d": 172800,
    "3d": 259200,
    "7d": 604800,
    "1w": 604800,
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
ORDER_TYPE_STOP_LIMIT = "stop_limit_order"
ORDER_TYPE_STOP_MARKET = "stop_market_order"

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

# Breakout offset (1 pip = 0.0001 for most USD pairs)
BREAKOUT_PIP_OFFSET = 0.0001
