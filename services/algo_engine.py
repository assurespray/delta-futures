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
from api.orders import is_order_gone, cancel_order  # CRITICAL: import robust methods
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
        self.strategy = DualSuperTrendStrategy()
        self.position_manager = PositionManager()
        self.order_monitor = OrderMonitor()
        self.logger_bot = logger_bot
        self.running_tasks = {}
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
        timeframe_map = {
            "1m": 60, "2m": 120, "3m": 180, "4m": 240, "5m": 300, "10m": 600, "15m": 900,
            "20m": 1200, "30m": 1800, "45m": 2700, "1h": 3600, "2h": 7200, "3h": 10800,
            "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400, "2d": 172800,
            "3d": 259200, "7d": 604800, "1w": 604800, "2w": 1209600, "1mo": 2592000,
        }
        sleep_seconds = timeframe_map.get(timeframe, 60)
        logger.debug(f"⏱️ Sleep time for timeframe '{timeframe}': {sleep_seconds}s ({sleep_seconds/60:.1f} minutes)")
        return sleep_seconds

    async def process_algo_setup(self, algo_setup: Dict[str, Any]):
        start_time = time.time()
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        asset = algo_setup['asset'].upper()
        timeframe = algo_setup['timeframe']
        current_position = algo_setup.get('current_position')

        self.signal_counts["total_checks"] += 1
        logger.info(f"🚀 Processing Algo: {setup_name} ({asset} @ {timeframe})")
        now = datetime.utcnow()
        if not is_at_candle_boundary(timeframe, now):
            next_boundary = get_next_boundary_time(timeframe, now)
            time_until = int((next_boundary - now).total_seconds())
            logger.debug(f"⏭️ [{setup_name}] Not at {timeframe} boundary - Next check in {time_until}s at {next_boundary.strftime('%H:%M:%S')} UTC")
            return

        self.signal_counts["boundary_hits"] += 1
        tf_display = get_timeframe_display_name(timeframe)
        logger.info(f"✅ [{setup_name}] At {tf_display} boundary - Processing {asset}")

        try:
            api_id = algo_setup['api_id']
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred:
                logger.error(f"❌ Failed to load credentials for setup {setup_name}")
                self.signal_counts["errors"] += 1
                await self.logger_bot.send_error(f"Failed to load API credentials for setup: {setup_name}")
                return

            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
            self.performance_stats["api_calls"] += 1

            logger.info(f"🔄 Processing {setup_name} ({asset} {timeframe})")
            max_retries = 20
            retry_count = 0
            indicator_result = None
            while retry_count < max_retries:
                indicator_result = await self.strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if indicator_result:
                    if retry_count > 0:
                        logger.info(f"✅ Indicators ready after {retry_count * 0.5:.1f}s")
                    break
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(0.5)
            if not indicator_result:
                logger.warning(f"⚠️ Failed to calculate indicators for {setup_name} after {max_retries * 0.5}s")
                self.signal_counts["errors"] += 1
                await client.close()
                return

            perusu_data = indicator_result['perusu']
            sirusu_data = indicator_result['sirusu']

            # === Robust Pending Order & Sirusu reversal logic ===
            pending_order_status = None
            pending_order_id = algo_setup.get('pending_entry_order_id')
            product_id = algo_setup.get('product_id')
            pending_side = algo_setup.get('pending_entry_side')
            if not current_position and pending_order_id and product_id:
                gone = await is_order_gone(client, pending_order_id, product_id)
                if gone:
                    logger.info(f"✅ Pending entry order {pending_order_id} was FILLED or CANCELLED (gone from orderbook/history)")
                    await update_algo_setup(setup_id, {
                        "pending_entry_order_id": None,
                        "pending_entry_side": None
                    })
                    pending_order_status = "filled"
                else:
                    # Sirusu reversal check
                    cached_sirusu = await get_indicator_cache(setup_id, "sirusu")
                    last_sirusu_signal = cached_sirusu.get('last_signal') if cached_sirusu else None
                    sirusu_flipped = False
                    if last_sirusu_signal is not None:
                        if pending_side == "long":
                            if sirusu_data['signal'] == -1 and last_sirusu_signal == 1:
                                sirusu_flipped = True
                        elif pending_side == "short":
                            if sirusu_data['signal'] == 1 and last_sirusu_signal == -1:
                                sirusu_flipped = True
                    if sirusu_flipped:
                        await cancel_order(client, pending_order_id)
                        await update_algo_setup(setup_id, {
                            "pending_entry_order_id": None,
                            "pending_entry_side": None
                        })
                        pending_order_status = "reversed"
                    else:
                        pending_order_status = "pending"

            if not current_position:
                if pending_order_status == "filled":
                    logger.info(f"✅ [TEST] Position opened via pending order - skipping entry check")
                    current_position = "long"  # Will be updated, but set for flow
                elif pending_order_status == "reversed":
                    logger.info(f"🔄 [TEST] Order cancelled - checking for new entry signal")

            if not current_position and not algo_setup.get('pending_entry_order_id'):
                cache_start = time.time()
                cached_perusu = await get_indicator_cache(setup_id, "perusu")
                cache_time = time.time() - cache_start
                last_perusu_signal = cached_perusu.get('last_signal') if cached_perusu else None
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
                    if success:
                        self.signal_counts["successful_entries"] += 1
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
                        await self.logger_bot.send_error(
                            f"Failed to execute entry for {setup_name}"
                        )
                else:
                    self.signal_counts["no_signals"] += 1
            elif current_position:
                exit_signal = self.strategy.generate_exit_signal(
                    setup_id,
                    current_position,
                    indicator_result
                )
                if exit_signal:
                    self.signal_counts["exit_signals"] += 1
                    logger.info(f"🚪 Exit signal detected for {setup_name}")
                    stop_loss_order_id = algo_setup.get("stop_loss_order_id")
                    sl_filled = False
                    if stop_loss_order_id and product_id:
                        sl_filled = await is_order_gone(client, stop_loss_order_id, product_id)
                    if sl_filled:
                        logger.warning(f"⚠️ Stop-loss already filled - skipping market exit")
                        logger.info(f"   execute_exit() will handle state sync")
                    exit_start = time.time()
                    success = await self.position_manager.execute_exit(
                        client=client,
                        algo_setup=algo_setup,
                        sirusu_signal_text=sirusu_data['signal_text']
                    )
                    exit_time = time.time() - exit_start
                    if success:
                        self.signal_counts["successful_exits"] += 1
                        await self.logger_bot.send_trade_exit(
                            setup_name=setup_name,
                            asset=asset,
                            direction=current_position,
                            sirusu_signal=sirusu_data['signal_text']
                        )
                    else:
                        self.signal_counts["failed_exits"] += 1
                        await self.logger_bot.send_error(
                            f"Failed to execute exit for {setup_name}"
                        )
            await self._cache_indicators(setup_id, perusu_data, sirusu_data, asset, timeframe)
            await client.close()
            elapsed = time.time() - start_time
            self._update_performance_stats(elapsed)
        except Exception as e:
            self.signal_counts["errors"] += 1
            elapsed = time.time() - start_time
            logger.error(f"❌ Exception processing algo setup {setup_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await self.logger_bot.send_error(f"Exception in {setup_name}: {str(e)[:200]}")

    def _update_performance_stats(self, elapsed: float):
        self.performance_stats["total_processing_time"] += elapsed
        self.performance_stats["min_processing_time"] = min(self.performance_stats["min_processing_time"], elapsed)
        self.performance_stats["max_processing_time"] = max(self.performance_stats["max_processing_time"], elapsed)
        if self.signal_counts["boundary_hits"] > 0:
            self.performance_stats["avg_processing_time"] = (
                self.performance_stats["total_processing_time"] / 
                self.signal_counts["boundary_hits"]
            )

    def _format_stats(self) -> str:
        return (
            f"Checks: {self.signal_counts['total_checks']} | "
            f"Boundaries: {self.signal_counts['boundary_hits']} | "
            f"Entry signals: {self.signal_counts['entry_signals']} | "
            f"Exit signals: {self.signal_counts['exit_signals']} | "
            f"Successful: {self.signal_counts['successful_entries']}E/{self.signal_counts['successful_exits']}X | "
            f"Errors: {self.signal_counts['errors']}"
        )

    def _format_performance(self) -> str:
        return (
            f"Avg: {self.performance_stats['avg_processing_time']:.3f}s | "
            f"Min: {self.performance_stats['min_processing_time']:.3f}s | "
            f"Max: {self.performance_stats['max_processing_time']:.3f}s | "
            f"Cache: {self.performance_stats['cache_hits']}H/{self.performance_stats['cache_misses']}M"
        )

    async def _cache_indicators(self, setup_id: str, perusu_data: Dict[str, Any],
                                sirusu_data: Dict[str, Any], asset: str, timeframe: str):
        try:
            cache_start = time.time()
            await upsert_indicator_cache({
                "algo_setup_id": setup_id,
                "indicator_name": "perusu",
                "asset": asset,
                "timeframe": timeframe,
                "last_signal": perusu_data['signal'],
                "last_value": perusu_data['supertrend_value'],
                "calculated_at": datetime.utcnow()
            })
            await upsert_indicator_cache({
                "algo_setup_id": setup_id,
                "indicator_name": "sirusu",
                "asset": asset,
                "timeframe": timeframe,
                "last_signal": sirusu_data['signal'],
                "last_value": sirusu_data['supertrend_value'],
                "calculated_at": datetime.utcnow()
            })
        except Exception as e:
            logger.error(f"❌ Failed to cache indicators: {e}")

    async def run_continuous_monitoring(self):
        logger.info("🚀 Starting continuous algo monitoring...")
        await self.logger_bot.send_info("🚀 Algo Engine Started - Monitoring active setups")
        loop_count = 0
        while True:
            try:
                loop_count += 1
                active_setups = await get_all_active_algo_setups()
                if not active_setups:
                    logger.debug("ℹ️ No active algo setups found")
                    await asyncio.sleep(60)
                    continue
                logger.debug(f"📊 [Loop {loop_count}] Checking {len(active_setups)} active algo setup(s)")
                tasks = []
                for setup in active_setups:
                    task = asyncio.create_task(self.process_algo_setup(setup))
                    tasks.append(task)
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        setup_name = active_setups[i].get('setup_name', 'Unknown')
                        logger.error(f"❌ Error processing {setup_name}: {result}")
                
                timeframes = [setup.get('timeframe', '1m') for setup in active_setups]
                timeframe_seconds = {
                    "1m": 60, "2m": 120, "3m": 180, "4m": 240, "5m": 300, "10m": 600,
                    "15m": 900, "20m": 1200, "30m": 1800, "45m": 2700, "1h": 3600,
                    "2h": 7200, "3h": 10800, "4h": 14400, "6h": 21600, "8h": 28800,
                    "12h": 43200, "1d": 86400
                }
                shortest_seconds = min(timeframe_seconds.get(tf, 60) for tf in timeframes)
                # In case of multiple with same min, pick first match (stable)
                shortest_tf = next(tf for tf in timeframes if timeframe_seconds.get(tf, 60) == shortest_seconds)

                now = datetime.utcnow()
                next_boundary = get_next_boundary_time(shortest_tf, now)
                time_until_boundary = (next_boundary - now).total_seconds()
                sleep_time = max(1, time_until_boundary + 0.5)
                logger.info(
                    f"💤 Next check at {next_boundary.strftime('%H:%M:%S')} UTC "
                    f"(sleeping {sleep_time:.1f}s for {shortest_tf} boundary)"
                )
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"❌ Exception in continuous monitoring: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await self.logger_bot.send_error(f"Monitoring loop error: {str(e)[:200]}")
                await asyncio.sleep(60)

async def reconcile_positions_on_startup():
    from database.mongodb import mongodb
    from database.crud import get_api_credential_by_id
    logger.info("🔍 Reconciling positions with exchange...")
    try:
        db = mongodb.get_db()
        open_positions = [pos async for pos in db.positions.find({"status": "OPEN"})]
        if not open_positions:
            logger.info("✅ No open positions to reconcile")
            return
        logger.info(f"📊 Found {len(open_positions)} open position(s) - checking exchange...")
        positions_by_api = {}
        for pos in open_positions:
            api_id = pos.get('api_id')
            if api_id not in positions_by_api:
                positions_by_api[api_id] = []
            positions_by_api[api_id].append(pos)
        for api_id, positions in positions_by_api.items():
            try:
                cred = await get_api_credential_by_id(api_id, decrypt=True)
                if not cred:
                    logger.warning(f"⚠️ Could not load credentials for API {api_id}")
                    continue
                client = DeltaExchangeClient(
                    api_key=cred['api_key'],
                    api_secret=cred['api_secret']
                )
                try:
                    exchange_open_positions = await client.get_open_positions()
                    exchange_ids = set(p.get('id') for p in exchange_open_positions)
                except Exception as e:
                    logger.warning(f"⚠️ Could not fetch exchange positions for {api_id}: {e}")
                    await client.close()
                    continue
                for db_pos in positions:
                    order_id = db_pos.get('order_id')
                    symbol = db_pos.get('symbol', 'UNKNOWN')
                    if order_id not in exchange_ids:
                        logger.warning(f"⚠️ Position {order_id} ({symbol}) not found on exchange")
                        logger.info(f"   Marking as CLOSED in DB (manual close detected)")
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


async def reconcile_positions_on_startup():
    """
    Sync open positions and pending orders from the exchange with the DB. 
    Place stop-loss if additional_protection is True and no SL is live.
    """
    import logging
    from api.delta_client import DeltaExchangeClient
    from api.positions import get_position_by_symbol
    from api.orders import get_open_orders
    from indicators.signal_generator import SignalGenerator
    from database.crud import get_all_active_algo_setups, update_algo_setup
    from datetime import datetime

    logger = logging.getLogger(__name__)
    client = DeltaExchangeClient()
    signal_generator = SignalGenerator()  # make sure this is async-compatible

    all_setups = await get_all_active_algo_setups()
    for setup in all_setups:
        symbol = setup.get("asset")
        setup_id = str(setup["_id"])
        product_id = setup.get("product_id")
        lot_size = setup.get("lot_size")
        addl_prot = setup.get("additional_protection", False)

        # 1. Check for live position
        position = await get_position_by_symbol(client, symbol)
        position_size = position.get("size", 0) if position else 0
        entry_price = position.get("entry_price") if position else None

        if position_size != 0:
            logger.info(f"Reconciling {symbol}: Open position {position_size} found on exchange")
            await update_algo_setup(setup_id, {
                "current_position": "long" if position_size > 0 else "short",
                "last_entry_price": entry_price,
                "position_lock_acquired": True,
                "last_signal_time": datetime.utcnow(),
            })

            # --- ACTIVITY RECORD CREATION ---
            # Only create if none exists for this setup (avoid duplicates on every restart)
            open_activity = await get_open_activity_by_setup(setup_id)
            if not open_activity:
                activity_data = {
                    "user_id": setup.get("user_id"),
                    "algo_setup_id": setup_id,
                    "algo_setup_name": setup.get("setup_name"),
                    "entry_time": datetime.utcnow(),   # Optional: use now, since real entry unknown
                    "entry_price": entry_price,
                    "direction": "long" if position_size > 0 else "short",
                    "lot_size": abs(position_size),
                    "asset": symbol,
                    "perusu_entry_signal": "reconciled",
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "is_closed": False,
                    "meta": {"reconciled": True}
                }
                await create_algo_activity(activity_data)

            # 2. Check open/pending orders (entry and stop-loss)
            open_orders = await get_open_orders(client, product_id)
            found_sl = False
            for order in open_orders or []:
                state = order.get("state")
                if state in ("open", "untriggered"):
                    order_type = order.get("order_type")
                    if order_type == "stop_market_order" and order.get("reduce_only"):
                        found_sl = True
                        await update_algo_setup(setup_id, {
                            "stop_loss_order_id": order.get("id"),
                        })

            # 3. Place stop-loss if required
            if addl_prot and not found_sl:
                # Use the current Sirusu value for new SL for safety
                sirusu_value = await signal_generator.calculate_current_sirusu_value(symbol)
                side = "long" if position_size > 0 else "short"
                from services.position_manager import PositionManager
                pm = PositionManager()
                sl_order_id = await pm._place_stop_loss_protection(
                    client, product_id, abs(position_size), side, sirusu_value, setup_id
                )
                logger.info(f"Stop-loss order placed for {symbol}: {sl_order_id}")

        else:
            # If no position, clear position fields
            await update_algo_setup(setup_id, {
                "current_position": None,
                "last_entry_price": None,
                "position_lock_acquired": False,
                "stop_loss_order_id": None,
            })
            
