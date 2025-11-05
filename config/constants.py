"""
Trading constants and application-wide configuration.

✅ CRITICAL FIXES:
- Added missing "3m" timeframe mapping (was blocking 3m trading)
- Complete Delta Exchange API timeframe mappings
- All timeframes in seconds for scheduler
- SuperTrend parameters (Perusu & Sirusu)
- Signal constants, order types, trading settings
"""

# ===== TIMEFRAME MAPPING FOR DELTA EXCHANGE API =====
# ✅ Maps user-requested timeframes to actual Delta Exchange resolutions
# ✅ Delta Exchange supports: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d only
# ✅ Maps other timeframes to nearest available (with comments)

TIMEFRAME_MAPPING = {
    # ===== MINUTES =====
    "1m": "1m",          # ✅ Delta supports
    "2m": "3m",          # ❌ Delta doesn't have 2m → use 3m
    "3m": "3m",          # ✅ Delta supports (CRITICAL - was missing!)
    "4m": "5m",          # ❌ Delta doesn't have 4m → use 5m
    "5m": "5m",          # ✅ Delta supports
    "10m": "15m",        # ❌ Delta doesn't have 10m → use 15m
    "15m": "15m",        # ✅ Delta supports
    "20m": "30m",        # ❌ Delta doesn't have 20m → use 30m
    "30m": "30m",        # ✅ Delta supports
    "45m": "1h",         # ❌ Delta doesn't have 45m → use 1h
    
    # ===== HOURS =====
    "1h": "1h",          # ✅ Delta supports
    "2h": "4h",          # ❌ Delta doesn't have 2h → use 4h
    "3h": "4h",          # ❌ Delta doesn't have 3h → use 4h
    "4h": "4h",          # ✅ Delta supports
    "6h": "1d",          # ❌ Delta doesn't have 6h → use 1d
    "8h": "1d",          # ❌ Delta doesn't have 8h → use 1d
    "12h": "1d",         # ❌ Delta doesn't have 12h → use 1d
    
    # ===== DAYS =====
    "1d": "1d",          # ✅ Delta supports
    "2d": "1d",          # ❌ Delta doesn't have 2d → use 1d
    "3d": "1d",          # ❌ Delta doesn't have 3d → use 1d
    "7d": "1d",          # ❌ Delta doesn't have 7d → use 1d
    "1w": "1d",          # ❌ Delta doesn't have 1w → use 1d
}

# ===== TIME IN FORCE OPTIONS =====
TIME_IN_FORCE_GTC = "gtc"     # Good-Till-Cancelled (default)
TIME_IN_FORCE_IOC = "ioc"     # Immediate Or Cancel
TIME_IN_FORCE_FOK = "fok"     # Fill Or Kill

# ===== TIMEFRAME IN SECONDS FOR SCHEDULER =====
# ✅ Used for calculating next check time
# ✅ Used for rate limiting and cache expiry
# ✅ Complete for ALL supported timeframes
# Add if not already present
CANDLE_CLOSE_BUFFER_SECONDS = 5

TIMEFRAME_SECONDS = {
    # ===== MINUTES =====
    "1m": 60,
    "2m": 120,
    "3m": 180,           # ✅ CRITICAL - was missing!
    "4m": 240,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "20m": 1200,
    "30m": 1800,
    "45m": 2700,
    
    # ===== HOURS =====
    "1h": 3600,
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

# ===== SUPERTREND INDICATOR PARAMETERS =====
# Perusu: Entry indicator (trend detector)
# Sirusu: Exit indicator (stop-loss)

PERUSU_ATR_LENGTH = 20        # ATR period for entry signal
PERUSU_FACTOR = 20            # ATR multiplier for bands (wider = fewer signals)

SIRUSU_ATR_LENGTH = 10        # ATR period for exit signal
SIRUSU_FACTOR = 10            # ATR multiplier for bands (tighter = faster exits)

# ===== TRADING DIRECTION =====
DIRECTION_BOTH = "both"               # Trade both LONG and SHORT
DIRECTION_LONG_ONLY = "long_only"     # Trade LONG only (no shorts)
DIRECTION_SHORT_ONLY = "short_only"   # Trade SHORT only (no longs)

# ===== ORDER TYPES =====
ORDER_TYPE_MARKET = "market_order"              # Immediate execution
ORDER_TYPE_LIMIT = "limit_order"                # Price-specific limit
ORDER_TYPE_STOP_LIMIT = "stop_limit_order"      # Stop-limit combination
ORDER_TYPE_STOP_MARKET = "stop_market_order"    # Stop → market execution

# ===== ORDER SIDES =====
ORDER_SIDE_BUY = "buy"                          # Long entry
ORDER_SIDE_SELL = "sell"                        # Short entry / Long exit

# ===== SUPERTREND SIGNAL CONSTANTS =====
# Used in indicator calculations
SIGNAL_UPTREND = 1                              # SuperTrend = Lower Band
SIGNAL_DOWNTREND = -1                           # SuperTrend = Upper Band

# ===== API RATE LIMITING =====
MAX_REQUESTS_PER_SECOND = 10                    # Delta Exchange API limit
REQUEST_RETRY_ATTEMPTS = 3                      # Retries on API failure
REQUEST_RETRY_DELAY = 2                         # Seconds between retries

# ===== DATA RETENTION =====
ALGO_ACTIVITY_RETENTION_DAYS = 3               # Clean up old activity logs

# ===== LOGGING CONFIGURATION =====
LOG_FILE_MAX_SIZE = 100 * 1024 * 1024           # 100MB per log file
LOG_FILE_BACKUP_COUNT = 5                       # Keep 5 backup logs

# ===== HEALTH CHECK =====
SELF_PING_INTERVAL = 300                        # Health check every 5 minutes
SELF_PING_FAIL_THRESHOLD = 3                    # Alert after 3 failed checks

# ===== CACHE CONFIGURATION =====
PRODUCT_CACHE_EXPIRY = 86400                    # Product list cache (24 hours)

# ===== BREAKOUT SETTINGS =====
# 1 pip offset for breakout orders
# Typical pip = 0.0001 for most USD pairs
# Adjust per asset: crypto (0.00001), indices (0.1), etc.

BREAKOUT_PIP_OFFSET = 0.0001                    # Standard pip (0.0001)

# ===== POSITION MANAGEMENT =====
DEFAULT_LOT_SIZE = 1                            # Default contract quantity
ORDER_TIMEOUT = 120                             # Order hold time (seconds)
MAX_POSITION_RETRIES = 3                        # Max retries for position checks

# ===== MARKET DATA =====
CANDLE_BATCH_SIZE = 100                         # Candles per API request
MAX_CANDLE_AGE_SECONDS = 300                    # Consider candle stale after 5m

# ===== FEATURE FLAGS =====
ENABLE_DEMO_MODE = False                        # Set True for paper trading
ENABLE_VERBOSE_LOGGING = True                   # Set False for production
