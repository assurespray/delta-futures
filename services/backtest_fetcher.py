"""
Backtest Data Fetcher - Memory-Safe Historical Candle Downloader

Downloads historical candles from Delta Exchange in paginated chunks
and writes them directly to local CSV files on disk, avoiding RAM overload.

Architecture:
    - Fetches candles in small time-window pages (max 2000 per API call)
    - Writes each page directly to a CSV file (append mode)
    - Never holds the full dataset in memory
    - Supports a progress callback for live UI updates

Usage:
    fetcher = BacktestFetcher()
    csv_path, total = await fetcher.fetch_and_cache(
        client, "BTCUSD", "1m",
        start_ts=1672531200, end_ts=1704067200,
        progress_callback=my_callback
    )
"""

import os
import csv
import logging
import asyncio
from typing import Optional, Callable, Awaitable, Tuple
from datetime import datetime, timezone

from api.delta_client import DeltaExchangeClient
from api.market_data import get_candles
from config.constants import TIMEFRAME_SECONDS, TIMEFRAME_MAPPING

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")
CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# Delta Exchange returns at most ~2000 candles per request.
# We use 1500 as a safe page size to avoid edge-case truncation.
PAGE_SIZE = 1500

# Minimum pause between consecutive API calls (seconds)
API_PAUSE = 0.15


# ---------------------------------------------------------------------------
# Helper: build a cache-file path
# ---------------------------------------------------------------------------
def _cache_path(symbol: str, timeframe: str) -> str:
    """Return the absolute path to the CSV cache file for a symbol/timeframe."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{symbol}_{timeframe}.csv")


# ---------------------------------------------------------------------------
# Helper: read existing cache metadata (row count & time range)
# ---------------------------------------------------------------------------
def get_cache_info(symbol: str, timeframe: str) -> Optional[dict]:
    """
    Return metadata about an existing cache file, or None if missing.

    Returns:
        {
            "path": str,
            "rows": int,
            "start_time": int,   # earliest Unix timestamp
            "end_time": int,     # latest Unix timestamp
        }
    """
    path = _cache_path(symbol, timeframe)
    if not os.path.exists(path):
        return None

    rows = 0
    first_time = None
    last_time = None

    try:
        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = int(row["time"])
                if first_time is None:
                    first_time = ts
                last_time = ts
                rows += 1
    except Exception as e:
        logger.error(f"[BT-FETCH] Error reading cache info: {e}")
        return None

    if rows == 0:
        return None

    return {
        "path": path,
        "rows": rows,
        "start_time": first_time,
        "end_time": last_time,
    }


def delete_cache(symbol: str, timeframe: str) -> bool:
    """Delete the cache file for a symbol/timeframe. Returns True if deleted."""
    path = _cache_path(symbol, timeframe)
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"[BT-FETCH] Deleted cache: {path}")
        return True
    return False


# ---------------------------------------------------------------------------
# Core: paginated fetch + write-to-disk
# ---------------------------------------------------------------------------
class BacktestFetcher:
    """
    Downloads historical candles from Delta Exchange and writes them
    directly to a CSV file on disk in a memory-efficient, paginated manner.
    """

    def __init__(self):
        self._abort = False

    def abort(self):
        """Signal the fetcher to stop downloading."""
        self._abort = True

    async def fetch_and_cache(
        self,
        client: DeltaExchangeClient,
        symbol: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
        progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
    ) -> Tuple[Optional[str], int]:
        """
        Fetch historical candles between *start_ts* and *end_ts* (Unix seconds)
        and write them to a local CSV file.

        Args:
            client:             Authenticated Delta Exchange client.
            symbol:             Trading symbol (e.g. "BTCUSD").
            timeframe:          Candle timeframe (e.g. "1m", "15m", "1h").
            start_ts:           Start Unix timestamp (inclusive).
            end_ts:             End Unix timestamp (inclusive).
            progress_callback:  Optional async function(fetched, estimated_total, status_msg).

        Returns:
            (csv_path, total_candles_written)  on success.
            (None, 0)                          on failure.
        """
        self._abort = False

        # Validate timeframe
        if timeframe not in TIMEFRAME_SECONDS:
            logger.error(f"[BT-FETCH] Unknown timeframe: {timeframe}")
            return None, 0

        seconds_per_candle = TIMEFRAME_SECONDS[timeframe]
        estimated_total = max(1, (end_ts - start_ts) // seconds_per_candle)

        csv_path = _cache_path(symbol, timeframe)

        # If a cache already covers the requested range, reuse it
        info = get_cache_info(symbol, timeframe)
        if info and info["start_time"] <= start_ts and info["end_time"] >= end_ts - seconds_per_candle:
            logger.info(
                f"[BT-FETCH] Cache hit for {symbol} {timeframe} "
                f"({info['rows']} candles). Skipping download."
            )
            if progress_callback:
                await progress_callback(info["rows"], info["rows"], "Cache hit - using existing data")
            return csv_path, info["rows"]

        # Wipe stale cache and start fresh
        delete_cache(symbol, timeframe)

        logger.info(
            f"[BT-FETCH] Starting download: {symbol} {timeframe} "
            f"from {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} "
            f"to {datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()} "
            f"(~{estimated_total} candles)"
        )

        total_written = 0
        seen_timestamps = set()   # deduplicate across pages
        current_start = start_ts
        consecutive_empty = 0
        page_number = 0

        # Open the CSV file in write mode with header
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

                # BACKWARD PAGINATION: Start from end_ts and walk backward to start_ts.
        current_end = end_ts
        consecutive_empty = 0
        
        # We will save each page to a separate temp file, then combine them in reverse order.
        temp_files = []

        while current_end > start_ts:
            if self._abort:
                logger.warning("[BT-FETCH] Download aborted by user.")
                # Cleanup temp files
                for tf in temp_files:
                    if os.path.exists(tf): os.remove(tf)
                return None, 0

            page_number += 1
            page_start = max(current_end - (PAGE_SIZE * seconds_per_candle), start_ts)

            try:
                candles = await get_candles(client=client, symbol=symbol, timeframe=timeframe, start_time=page_start, end_time=current_end, limit=PAGE_SIZE)
            except Exception as e:
                logger.error(f"[BT-FETCH] API error on page {page_number}: {e}")
                await asyncio.sleep(1)
                consecutive_empty += 1
                if consecutive_empty > 5:
                    logger.error("[BT-FETCH] Too many consecutive errors, stopping.")
                    break
                current_end = page_start
                continue

            if not candles or len(candles) == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.warning("[BT-FETCH] Hit empty data in the past (Genesis reached). Stopping backward fetch safely.")
                    break
                current_end = page_start
                await asyncio.sleep(API_PAUSE)
                continue

            consecutive_empty = 0

            # Deduplicate within the chunk
            new_rows = []
            for c in candles:
                ts = c["time"]
                if ts not in seen_timestamps and start_ts <= ts <= end_ts:
                    seen_timestamps.add(ts)
                    new_rows.append({
                        "time": ts, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]
                    })

            if new_rows:
                new_rows.sort(key=lambda r: r["time"])
                
                # Write to a temporary file for this specific page
                temp_file = f"{csv_path}.part{page_number}"
                temp_files.append(temp_file)
                
                with open(temp_file, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                    # No header in temp files
                    writer.writerows(new_rows)

                total_written += len(new_rows)

            if progress_callback:
                pct_msg = f"Fetching historical data... ({total_written}/{estimated_total} candles)"
                try:
                    await progress_callback(total_written, estimated_total, pct_msg)
                except Exception:
                    pass

            earliest_ts = min(c["time"] for c in candles)
            next_end = earliest_ts - seconds_per_candle

            if next_end >= current_end:
                current_end = page_start
            else:
                current_end = next_end

            await asyncio.sleep(API_PAUSE)

        # Combine temp files in reverse order (oldest to newest)
        if total_written > 0:
            with open(csv_path, "w", newline="") as f_out:
                writer = csv.DictWriter(f_out, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                
                # Reverse the list of temp files so the oldest chunk is written first
                for tf in reversed(temp_files):
                    if os.path.exists(tf):
                        with open(tf, "r", newline="") as f_in:
                            # Read raw lines and write them
                            f_out.write(f_in.read())
                        os.remove(tf)  # Cleanup temp file after merging

        logger.info(
            f"[BT-FETCH] Download complete: {symbol} {timeframe} "
            f"- {total_written} candles written to {csv_path}"
        )

        if progress_callback:
            try:
                await progress_callback(total_written, total_written, "Download complete!")
            except Exception:
                pass

        return csv_path, total_written




# ---------------------------------------------------------------------------
# Convenience: estimate candle count for a given duration
# ---------------------------------------------------------------------------
def estimate_candle_count(timeframe: str, days: int) -> int:
    """Return the approximate number of candles for *days* trading days."""
    spc = TIMEFRAME_SECONDS.get(timeframe, 60)
    return (days * 86400) // spc


def estimate_download_time_seconds(timeframe: str, days: int) -> int:
    """
    Rough estimate of how long the download will take (in seconds).
    Based on ~1500 candles per API page and ~0.3s per page.
    """
    total_candles = estimate_candle_count(timeframe, days)
    pages = max(1, total_candles // PAGE_SIZE)
    return int(pages * 0.3) + 2  # +2s buffer
