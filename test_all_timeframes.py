"""Test all Delta Exchange India timeframes."""
from datetime import datetime
from utils.timeframe import (
    is_at_candle_boundary, 
    get_next_boundary_time,
    get_timeframe_display_name
)

# All Delta Exchange India timeframes
all_timeframes = [
    "1m", "2m", "3m", "5m", "10m", "15m", "30m",
    "1h", "2h", "3h", "4h", "6h", "12h",
    "1d", "1w", "7d"
]

# Test times
test_times = [
    datetime(2025, 10, 27, 10, 0, 0),   # 10:00:00
    datetime(2025, 10, 27, 10, 2, 0),   # 10:02:00
    datetime(2025, 10, 27, 10, 3, 0),   # 10:03:00
    datetime(2025, 10, 27, 10, 5, 0),   # 10:05:00
    datetime(2025, 10, 27, 10, 10, 0),  # 10:10:00
    datetime(2025, 10, 27, 10, 15, 0),  # 10:15:00
    datetime(2025, 10, 27, 10, 30, 0),  # 10:30:00
    datetime(2025, 10, 27, 12, 0, 0),   # 12:00:00
    datetime(2025, 10, 27, 15, 0, 0),   # 15:00:00 (3h boundary)
]

print("=" * 80)
print("DELTA EXCHANGE INDIA - TIMEFRAME BOUNDARY TEST")
print("=" * 80)

for tf in all_timeframes:
    display_name = get_timeframe_display_name(tf)
    print(f"\n{tf.upper()} ({display_name}):")
    print("-" * 60)
    
    for test_time in test_times:
        is_boundary = is_at_candle_boundary(tf, test_time)
        next_time = get_next_boundary_time(tf, test_time)
        status = "✅ CHECK" if is_boundary else "⏭️  SKIP"
        
        print(f"  {test_time.strftime('%H:%M')} → {status:12} | Next: {next_time.strftime('%H:%M')}")

print("\n" + "=" * 80)
