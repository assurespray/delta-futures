"""Core trading engine for algo execution - ENHANCED WITH DYNAMIC SLEEP."""
import logging
import asyncio
import time
from typing import Dict, Any, Optional
from datetime import datetime
from database.crud import (
    get_all_active_algo_setups, get_api_credential_by_id,
    update_algo_setup, upsert_indicator_cache, get_indicator_cache,
    get_algo_setup_by_id
)
from api.delta_client import DeltaExchangeClient
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.position_manager import PositionManager
from strategy.order_monitor import OrderMonitor
from services.logger_bot import LoggerBot
from utils.timeframe import (
    is_at_candle_boundary,
    get_next_boundary_time,
    get_timeframe_display_name
)

logger = logging.getLogger(__name__)


class AlgoEngine:
    """Main trading engine for executing algo strategies - ENHANCED WITH DYNAMIC SLEEP."""
    
    def __init__(self, logger_bot: LoggerBot):
        """
        Initialize algo engine with testing instrumentation.
        
        Args:
            logger_bot: Logger bot instance for notifications
        """
        self.strategy = DualSuperTrendStrategy()
        self.position_manager = PositionManager()
        self.order_monitor = OrderMonitor()
        self.logger_bot = logger_bot
        self.running_tasks = {}
        
        # ✅ TESTING: Signal counters
        self.signal_counts = {
            "total_checks": 0,
            "boundary_hits": 0,
            "entry_signals": 0,
            "exit_signals": 0,
            "successful_entries": 0,
            "successful_exits": 0,
            "failed_entries": 0,
            "failed_exits": 0,
            "no_signals": 0,
            "errors": 0
        }
        
        # ✅ TESTING: Performance tracking
        self.performance_stats = {
            "total_processing_time": 0.0,
            "avg_processing_time": 0.0,
            "min_processing_time": float('inf'),
            "max_processing_time": 0.0,
            "api_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
    
    def get_sleep_time_seconds(self, timeframe: str) -> int:
        """
        ✅ COMPREHENSIVE: Convert timeframe to sleep time in seconds.
    
        Maps ALL common timeframe strings to their corresponding sleep duration.
        This ensures the bot processes exactly once per candle period.
    
        Args:
            timeframe: Timeframe string (e.g., "1m", "5m", "15m", "1h", "1d", "1w")
        
        Returns:
            Sleep time in seconds
        """
        timeframe_map = {
            # ===== MINUTES =====
            "1m": 60,              # 1 minute
            "2m": 120,             # 2 minutes
            "3m": 180,             # 3 minutes
            "4m": 240,             # 4 minutes
            "5m": 300,             # 5 minutes
            "10m": 600,            # 10 minutes
            "15m": 900,            # 15 minutes
            "20m": 1200,           # 20 minutes
            "30m": 1800,           # 30 minutes
            "45m": 2700,           # 45 minutes
        
            # ===== HOURS =====
            "1h": 3600,            # 1 hour
            "2h": 7200,            # 2 hours
            "3h": 10800,           # 3 hours
            "4h": 14400,           # 4 hours
            "6h": 21600,           # 6 hours
            "8h": 28800,           # 8 hours
            "12h": 43200,          # 12 hours
        
            # ===== DAYS =====
            "1d": 86400,           # 1 day (24 hours)
            "2d": 172800,          # 2 days
            "3d": 259200,          # 3 days
            "7d": 604800,          # 7 days (1 week)
        
            # ===== WEEKS & MONTHS =====
            "1w": 604800,          # 1 week
            "2w": 1209600,         # 2 weeks
            "1mo": 2592000,        # 1 month (30 days)
        }
    
        # Get sleep time from map, default to 60s if unknown
        sleep_seconds = timeframe_map.get(timeframe, 60)
        logger.debug(f"⏱️ Sleep time for timeframe '{timeframe}': {sleep_seconds}s ({sleep_seconds/60:.1f} minutes)")
    
        return sleep_seconds

    
    async def process_algo_setup(self, algo_setup: Dict[str, Any]):
        """
        Process a single algo setup with duplicate filtering.
    
        ✅ FIXED: Skips screener duplicates (algo has priority)
    
        Args:
            algo_setup: Algo setup configuration
        """
        # ✅ TESTING: Start performance timer
        start_time = time.time()
    
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        asset = algo_setup['asset'].upper()
        timeframe = algo_setup['timeframe']
        direction = algo_setup['direction']
        current_position = algo_setup.get('current_position')
    
        # Increment total checks
        self.signal_counts["total_checks"] += 1
    
        # ✅ NO DUPLICATE CHECK HERE!
        # Algo ALWAYS trades - it has priority!
        # Screener will skip duplicates instead.
    
        logger.info(f"🚀 Processing Algo: {setup_name} ({asset} @ {timeframe})")
    
        # ✅ CRITICAL: CHECK IF AT CANDLE BOUNDARY
        now = datetime.utcnow()
        if not is_at_candle_boundary(timeframe, now):
            next_boundary = get_next_boundary_time(timeframe, now)
            time_until = int((next_boundary - now).total_seconds())
        
            logger.debug(
                f"⏭️ [{setup_name}] Not at {timeframe} boundary - "
                f"Next check in {time_until}s at {next_boundary.strftime('%H:%M:%S')} UTC"
            )
            return
    
        # ✅ TESTING: Log boundary hit
        self.signal_counts["boundary_hits"] += 1
        tf_display = get_timeframe_display_name(timeframe)
        logger.info(f"✅ [{setup_name}] At {tf_display} boundary - Processing {asset}")
        # ✅ COMMENTED OUT - SAVES ~0.01s
        # logger.info(f"📊 [TEST] Session stats: {self._format_stats()}")
    
        try:
            # Get API credentials
            api_id = algo_setup['api_id']
            cred = await get_api_credential_by_id(api_id, decrypt=True)
        
            if not cred:
                logger.error(f"❌ Failed to load credentials for setup {setup_name}")
                self.signal_counts["errors"] += 1
                await self.logger_bot.send_error(
                    f"Failed to load API credentials for setup: {setup_name}"
                )
                return
        
            # Create Delta Exchange client
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
        
            # ✅ TESTING: Track API call
            self.performance_stats["api_calls"] += 1
        
            # Calculate indicators
            # ✅ NEW CODE (Smart retry loop - ONLY CHANGE)
            logger.info(f"🔄 Processing {setup_name} ({asset} {timeframe})")

            # ✅ CRITICAL FIX: Smart retry loop (up to 10 seconds)
            max_retries = 20  # 20 × 0.5s = 10 seconds max
            retry_count = 0
            indicator_result = None

            while retry_count < max_retries:
                indicator_result = await self.strategy.calculate_indicators(
                    client, asset, timeframe
                )
    
                if indicator_result:
                    # ✅ SUCCESS - Data ready!
                    if retry_count > 0:
                        logger.info(f"✅ Indicators ready after {retry_count * 0.5:.1f}s")
                    break
    
                retry_count += 1
    
                if retry_count < max_retries:
                    await asyncio.sleep(0.5)  # Sleep 500ms before retry

            if not indicator_result:
                logger.warning(f"⚠️ Failed to calculate indicators for {setup_name} after {max_retries * 0.5}s")
                self.signal_counts["errors"] += 1
                await client.close()
                return

        
        
            perusu_data = indicator_result['perusu']
            sirusu_data = indicator_result['sirusu']
        
            # ✅ COMMENTED OUT - SAVES ~0.03s
            # logger.info(f"📈 [TEST] Indicator Details:")
            # logger.info(f"   Perusu: {perusu_data['signal_text']} (Value: ${perusu_data['supertrend_value']:.5f})")
            # logger.info(f"   Sirusu: {sirusu_data['signal_text']} (Value: ${sirusu_data['supertrend_value']:.5f})")
            # logger.info(f"   Price: ${perusu_data['latest_close']:.5f}")
            # logger.info(f"   ATR: {perusu_data['atr']:.6f}")
        
            # ✅ CHECK PENDING ENTRY ORDER (if not in position)
            if not current_position:
                pending_order_status = await self.order_monitor.check_pending_entry_order(
                    client=client,
                    algo_setup=algo_setup,
                    current_perusu_signal=perusu_data['signal'],
                    sirusu_value=sirusu_data['supertrend_value'],
                    logger_bot=self.logger_bot
                )
            
                # ✅ COMMENTED OUT - SAVES ~0.01s
                # if pending_order_status:
                #     logger.info(f"📋 [TEST] Pending order status: {pending_order_status}")
            
                # If order was just filled, position is now open
                if pending_order_status == "filled":
                    logger.info(f"✅ [TEST] Position opened via pending order - skipping entry check")
                    current_position = "long"  # Will be updated in DB, but set for exit check
            
                # If order was cancelled due to reversal, continue to check for new signal
                elif pending_order_status == "reversed":
                    logger.info(f"🔄 [TEST] Order cancelled - checking for new entry signal")
        
            # ✅ CHECK FOR ENTRY SIGNAL (only if no position AND no pending order)
            if not current_position and not algo_setup.get('pending_entry_order_id'):
                # Get last Perusu signal from cache BEFORE updating
                cache_start = time.time()
                cached_perusu = await get_indicator_cache(setup_id, "perusu")
                cache_time = time.time() - cache_start
            
                last_perusu_signal = cached_perusu.get('last_signal') if cached_perusu else None
            
                # ✅ COMMENTED OUT - SAVES ~0.02s
                # # ✅ TESTING: Track cache hit/miss
                # if cached_perusu:
                #     self.performance_stats["cache_hits"] += 1
                #     logger.info(f"💾 [TEST] Cache HIT - Last signal: {last_perusu_signal} ({cache_time:.3f}s)")
                # else:
                #     self.performance_stats["cache_misses"] += 1
                #     logger.info(f"💾 [TEST] Cache MISS - First run ({cache_time:.3f}s)")
                
                # Track cache stats without logging
                if cached_perusu:
                    self.performance_stats["cache_hits"] += 1
                else:
                    self.performance_stats["cache_misses"] += 1
            
                entry_signal = self.strategy.generate_entry_signal(
                    setup_id,
                    last_perusu_signal,
                    indicator_result
                )
            
                if entry_signal:
                    self.signal_counts["entry_signals"] += 1
                    logger.info(f"🚀 Entry signal detected for {setup_name}: {entry_signal['side'].upper()}")
                
                    # ✅ COMMENTED OUT - SAVES ~0.03s
                    # logger.info(f"🎯 [TEST] Entry Signal Details:")
                    # logger.info(f"   Side: {entry_signal['side'].upper()}")
                    # logger.info(f"   Trigger: Perusu flip from {last_perusu_signal} to {perusu_data['signal']}")
                    # logger.info(f"   Entry Price: ${perusu_data['latest_close']:.5f}")
                    # logger.info(f"   Breakout Trigger: ${entry_signal.get('trigger_price', 0):.5f}")
                    # logger.info(f"   Stop Loss: ${sirusu_data['supertrend_value']:.5f}")
                    # logger.info(f"   Lot Size: {algo_setup['lot_size']}")
                
                    # Execute entry
                    entry_start = time.time()
                    success = await self.position_manager.place_breakout_entry_order(
                        client=client,
                        algo_setup=algo_setup,
                        entry_side=entry_signal['side'],
                        breakout_price=entry_signal.get('trigger_price', perusu_data['latest_close']),
                        sirusu_value=sirusu_data['supertrend_value'],
                        immediate=entry_signal.get('immediate', False)
                    )

                    entry_time = time.time() - entry_start
                
                    # ✅ COMMENTED OUT - SAVES ~0.01s
                    # logger.info(f"⏱️ [TEST] Entry execution: {entry_time:.3f}s")
                    
                    if success:
                        self.signal_counts["successful_entries"] += 1
                        # ✅ COMMENTED OUT - SAVES ~0.01s
                        # logger.info(f"✅ [TEST] Entry successful! Total entries: {self.signal_counts['successful_entries']}")
                    
                        await self.logger_bot.send_trade_entry(
                            setup_name=setup_name,
                            asset=asset,
                            direction=entry_signal['side'],
                            entry_price=perusu_data['latest_close'],
                            lot_size=algo_setup['lot_size'],
                            perusu_signal=perusu_data['signal_text'],
                            sirusu_sl=sirusu_data['supertrend_value']
                        )
                    else:
                        self.signal_counts["failed_entries"] += 1
                        # ✅ COMMENTED OUT - SAVES ~0.01s
                        # logger.error(f"❌ [TEST] Entry failed! Total failures: {self.signal_counts['failed_entries']}")
                    
                        await self.logger_bot.send_error(
                            f"Failed to execute entry for {setup_name}"
                        )
            
                else:
                    self.signal_counts["no_signals"] += 1
                    # ✅ COMMENTED OUT - SAVES ~0.02s
                    # logger.info(f"⏭️ [TEST] No entry signal - Waiting for Perusu flip")
                    # logger.info(f"   Current: {perusu_data['signal_text']} ({perusu_data['signal']})")
                    # logger.info(f"   Cached: {last_perusu_signal}")
        
            # ✅ CHECK FOR EXIT SIGNAL (only if in position)
            elif current_position:
                # ✅ COMMENTED OUT - SAVES ~0.01s
                # logger.info(f"📍 [TEST] In position: {current_position.upper()} - Checking exit conditions")
            
                exit_signal = self.strategy.generate_exit_signal(
                    setup_id,
                    current_position,
                    indicator_result
                )
            
                if exit_signal:
                    self.signal_counts["exit_signals"] += 1
                    logger.info(f"🚪 Exit signal detected for {setup_name}")
                
                    # ✅ COMMENTED OUT - SAVES ~0.02s
                    # logger.info(f"🎯 [TEST] Exit Signal Details:")
                    # logger.info(f"   Position: {current_position.upper()}")
                    # logger.info(f"   Trigger: Sirusu flip ({sirusu_data['signal_text']})")
                    # logger.info(f"   Exit Price: ${sirusu_data['supertrend_value']:.5f}")

                    # ✅ CRITICAL: Check if stop-loss already filled BEFORE exit
                    from api.orders import check_stop_loss_filled
        
                    sl_filled = await check_stop_loss_filled(
                        client,
                        algo_setup.get("stop_loss_order_id"),
                        algo_setup.get("product_id")
                    )
        
                    if sl_filled:
                        logger.warning(f"⚠️ Stop-loss already filled - skipping market exit")
                        logger.info(f"   execute_exit() will handle state sync")
            
                    # Execute exit
                    exit_start = time.time()
                    success = await self.position_manager.execute_exit(
                        client=client,
                        algo_setup=algo_setup,
                        sirusu_signal_text=sirusu_data['signal_text']
                    )
                    exit_time = time.time() - exit_start
                
                    # ✅ COMMENTED OUT - SAVES ~0.01s
                    # logger.info(f"⏱️ [TEST] Exit execution: {exit_time:.3f}s")
                
                    if success:
                        self.signal_counts["successful_exits"] += 1
                        # ✅ COMMENTED OUT - SAVES ~0.01s
                        # logger.info(f"✅ [TEST] Exit successful! Total exits: {self.signal_counts['successful_exits']}")
                    
                        await self.logger_bot.send_trade_exit(
                            setup_name=setup_name,
                            asset=asset,
                            direction=current_position,
                            sirusu_signal=sirusu_data['signal_text']
                        )
                    else:
                        self.signal_counts["failed_exits"] += 1
                        # ✅ COMMENTED OUT - SAVES ~0.01s
                        # logger.error(f"❌ [TEST] Exit failed! Total failures: {self.signal_counts['failed_exits']}")
                    
                        await self.logger_bot.send_error(
                            f"Failed to execute exit for {setup_name}"
                        )
                else:
                    # ✅ COMMENTED OUT - SAVES ~0.02s
                    # logger.info(f"⏭️ [TEST] No exit signal - Holding {current_position.upper()} position")
                    # logger.info(f"   Sirusu: {sirusu_data['signal_text']} (waiting for flip)")
                    pass
        
            # ✅ Cache indicator values AFTER signal detection
            await self._cache_indicators(setup_id, perusu_data, sirusu_data, asset, timeframe)
        
            await client.close()
        
            # ✅ COMMENTED OUT - SAVES ~0.02s
            # # ✅ TESTING: Calculate and log total processing time
            # elapsed = time.time() - start_time
            # self._update_performance_stats(elapsed)
            # logger.info(f"⏱️ [TEST] Total processing time: {elapsed:.3f}s")
            # logger.info(f"📊 [TEST] Performance stats: {self._format_performance()}")
            
            # Still track stats without logging
            elapsed = time.time() - start_time
            self._update_performance_stats(elapsed)
        
        except Exception as e:
            self.signal_counts["errors"] += 1
            elapsed = time.time() - start_time
        
            logger.error(f"❌ Exception processing algo setup {setup_name}: {e}")
            # ✅ COMMENTED OUT - SAVES ~0.01s
            # logger.error(f"⏱️ [TEST] Failed after {elapsed:.3f}s")
            import traceback
            logger.error(traceback.format_exc())
            await self.logger_bot.send_error(
                f"Exception in {setup_name}: {str(e)[:200]}"
            )
    
    def _update_performance_stats(self, elapsed: float):
        """Update performance statistics."""
        self.performance_stats["total_processing_time"] += elapsed
        self.performance_stats["min_processing_time"] = min(
            self.performance_stats["min_processing_time"], 
            elapsed
        )
        self.performance_stats["max_processing_time"] = max(
            self.performance_stats["max_processing_time"], 
            elapsed
        )
        
        # Calculate average
        if self.signal_counts["boundary_hits"] > 0:
            self.performance_stats["avg_processing_time"] = (
                self.performance_stats["total_processing_time"] / 
                self.signal_counts["boundary_hits"]
            )
    
    def _format_stats(self) -> str:
        """Format signal statistics for logging."""
        return (
            f"Checks: {self.signal_counts['total_checks']} | "
            f"Boundaries: {self.signal_counts['boundary_hits']} | "
            f"Entry signals: {self.signal_counts['entry_signals']} | "
            f"Exit signals: {self.signal_counts['exit_signals']} | "
            f"Successful: {self.signal_counts['successful_entries']}E/{self.signal_counts['successful_exits']}X | "
            f"Errors: {self.signal_counts['errors']}"
        )
    
    def _format_performance(self) -> str:
        """Format performance statistics for logging."""
        return (
            f"Avg: {self.performance_stats['avg_processing_time']:.3f}s | "
            f"Min: {self.performance_stats['min_processing_time']:.3f}s | "
            f"Max: {self.performance_stats['max_processing_time']:.3f}s | "
            f"Cache: {self.performance_stats['cache_hits']}H/{self.performance_stats['cache_misses']}M"
        )
    
    async def _cache_indicators(self, setup_id: str, perusu_data: Dict[str, Any],
                                sirusu_data: Dict[str, Any], asset: str, timeframe: str):
        """
        Cache indicator values in database.
        
        Args:
            setup_id: Algo setup ID
            perusu_data: Perusu indicator data
            sirusu_data: Sirusu indicator data
            asset: Trading asset
            timeframe: Timeframe
        """
        try:
            cache_start = time.time()
            
            # Cache Perusu
            await upsert_indicator_cache({
                "algo_setup_id": setup_id,
                "indicator_name": "perusu",
                "asset": asset,
                "timeframe": timeframe,
                "last_signal": perusu_data['signal'],
                "last_value": perusu_data['supertrend_value'],
                "calculated_at": datetime.utcnow()
            })
            
            # Cache Sirusu
            await upsert_indicator_cache({
                "algo_setup_id": setup_id,
                "indicator_name": "sirusu",
                "asset": asset,
                "timeframe": timeframe,
                "last_signal": sirusu_data['signal'],
                "last_value": sirusu_data['supertrend_value'],
                "calculated_at": datetime.utcnow()
            })
            
            cache_time = time.time() - cache_start
            # ✅ COMMENTED OUT - SAVES ~0.01s
            # logger.info(f"💾 [TEST] Cache update completed ({cache_time:.3f}s)")
            
        except Exception as e:
            logger.error(f"❌ Failed to cache indicators: {e}")
    
    async def run_continuous_monitoring(self):
        """
        ✅ FIXED: Run continuous monitoring loop with BOUNDARY ALIGNMENT.
        Sleeps until NEXT candle boundary, not fixed duration.
        """
        logger.info("🚀 Starting continuous algo monitoring...")
        await self.logger_bot.send_info("🚀 Algo Engine Started - Monitoring active setups")
    
        loop_count = 0
    
        while True:
            try:
                loop_count += 1
            
                # Get all active algo setups
                active_setups = await get_all_active_algo_setups()
            
                if not active_setups:
                    logger.debug("ℹ️ No active algo setups found")
                    await asyncio.sleep(60)
                    continue
            
                logger.debug(f"📊 [Loop {loop_count}] Checking {len(active_setups)} active algo setup(s)")
                
                # Process each setup
                tasks = []
                for setup in active_setups:
                    task = asyncio.create_task(self.process_algo_setup(setup))
                    tasks.append(task)
            
                # Wait for all processing to complete
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Log any exceptions
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        setup_name = active_setups[i].get('setup_name', 'Unknown')
                        logger.error(f"❌ Error processing {setup_name}: {result}")
            
                # ✅ CRITICAL FIX: Calculate time until NEXT boundary
                first_setup = active_setups[0]
                timeframe = first_setup.get('timeframe', '1m')
            
                now = datetime.utcnow()
                next_boundary = get_next_boundary_time(timeframe, now)
            
                # Calculate seconds until next boundary
                time_until_boundary = (next_boundary - now).total_seconds()
            
                # Add 0.5 seconds buffer to ensure we're past the boundary
                sleep_time = max(1, time_until_boundary + 0.5)
                
                logger.info(
                    f"💤 Next check at {next_boundary.strftime('%H:%M:%S')} UTC "
                    f"(sleeping {sleep_time:.1f}s for {timeframe} boundary)"
                )
            
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"❌ Exception in continuous monitoring: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await self.logger_bot.send_error(f"Monitoring loop error: {str(e)[:200]}")
                await asyncio.sleep(60)


async def reconcile_positions_on_startup():
    """
    ✅ FIX: Verify open positions exist on exchange
    Called on bot startup - handles manually closed positions
    """
    from database.mongodb import mongodb
    from database.crud import get_api_credential_by_id
    
    logger.info("🔍 Reconciling positions with exchange...")
    
    try:
        # Get DB instance
        db = mongodb.get_db()
        
        # Get all open positions from DB
        # ✅ CORRECT - Use async list comprehension
        open_positions = [pos async for pos in db.positions.find({"status": "OPEN"})]

        
        if not open_positions:
            logger.info("✅ No open positions to reconcile")
            return
        
        logger.info(f"📊 Found {len(open_positions)} open position(s) - checking exchange...")
        
        # Group by API credential to minimize client creation
        positions_by_api = {}
        for pos in open_positions:
            api_id = pos.get('api_id')
            if api_id not in positions_by_api:
                positions_by_api[api_id] = []
            positions_by_api[api_id].append(pos)
        
        # Process each API credential's positions
        for api_id, positions in positions_by_api.items():
            try:
                # Get credentials
                cred = await get_api_credential_by_id(api_id, decrypt=True)
                if not cred:
                    logger.warning(f"⚠️ Could not load credentials for API {api_id}")
                    continue
                
                # Create client
                client = DeltaExchangeClient(
                    api_key=cred['api_key'],
                    api_secret=cred['api_secret']
                )
                
                # Get exchange positions
                try:
                    exchange_open_positions = await client.get_open_positions()
                    exchange_ids = set(p.get('id') for p in exchange_open_positions)
                except Exception as e:
                    logger.warning(f"⚠️ Could not fetch exchange positions for {api_id}: {e}")
                    await client.close()
                    continue
                
                # Check each position
                for db_pos in positions:
                    order_id = db_pos.get('order_id')
                    symbol = db_pos.get('symbol', 'UNKNOWN')
                    
                    if order_id not in exchange_ids:
                        # ✅ Position was closed manually
                        logger.warning(f"⚠️ Position {order_id} ({symbol}) not found on exchange")
                        logger.info(f"   Marking as CLOSED in DB (manual close detected)")
                        
                        # Update DB to CLOSED
                        db.positions.update_one(
                            {"_id": db_pos['_id']},
                            {
                                "$set": {
                                    "status": "CLOSED",
                                    "closed_reason": "Manual close (position not found on exchange)",
                                    "closed_at": datetime.utcnow(),
                                    "detected_at_startup": True
                                }
                            }
                        )
                        logger.info(f"   ✅ Status updated to CLOSED")
                    else:
                        logger.info(f"✅ Position {order_id} ({symbol}) verified on exchange")
                
                await client.close()
                
            except Exception as e:
                logger.error(f"❌ Error reconciling positions for API {api_id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        logger.info("✅ Position reconciliation completed")
        
    except Exception as e:
        logger.error(f"❌ Failed to reconcile positions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
