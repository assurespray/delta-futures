import logging
import asyncio
import time
import json
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from database.crud import (
    get_all_active_screener_setups,
    get_open_trade_states,
    get_pending_entry_trade_states,
    get_trade_state_by_id,
    update_trade_state,
    get_all_active_algo_setups, get_api_credential_by_id,
    update_algo_setup, save_indicator_cache,
    get_algo_setup_by_id, get_last_strategy_state
)
from api.delta_client import DeltaExchangeClient
from api.orders import is_order_gone, cancel_order
from strategy.factory import StrategyFactory
from strategy.position_manager import PositionManager
from strategy.paper_trader import paper_trader, is_paper_trade
from api.positions import get_ticker_mark_price
from services.logger_bot import LoggerBot
from utils.timeframe import (
    is_at_candle_boundary,
    get_next_boundary_time,
    get_timeframe_display_name
)

logger = logging.getLogger(__name__)

class AlgoEngine:
    """Strategy-agnostic trading engine. Delegates all indicator and signal
    logic to the strategy returned by StrategyFactory."""

    def __init__(self, logger_bot: LoggerBot):
        
        self.position_manager = PositionManager()
        self.logger_bot = logger_bot
        self.running_tasks = {}
        self._strategy_cache: Dict[str, Tuple[str, str, Any]] = {}  # setup_id -> (type, params_hash, instance)
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
        logger.debug(f"Sleep time for timeframe '{timeframe}': {sleep_seconds}s ({sleep_seconds/60:.1f} minutes)")
        return sleep_seconds

    def _get_strategy(self, setup_id: str, strategy_type: str, params: dict = None):
        """Return a cached strategy instance for this setup, or create one.
        
        Keyed by setup_id so each setup gets its own instance with persistent
        dedup caches (_last_processed_candle_time, etc.). If strategy_type or
        params change (user edited the setup), the instance is recreated.
        """
        params = params or {}
        params_hash = json.dumps(params, sort_keys=True, default=str)
        cached = self._strategy_cache.get(setup_id)
        if cached and cached[0] == strategy_type and cached[1] == params_hash:
            return cached[2]
        instance = StrategyFactory.get_strategy(strategy_type, params)
        self._strategy_cache[setup_id] = (strategy_type, params_hash, instance)
        return instance


    async def run_continuous_monitoring(self):
        """
        Background loop to monitor active setups and open trades on candle boundaries.
        """
        logger.info("Starting boundary-aligned algo monitoring loop.")
        
        while True:
            try:
                active_setups = await get_all_active_algo_setups()
                open_trades = await get_open_trade_states()
                pending_trades = await get_pending_entry_trade_states()
                
                if not active_setups and not open_trades and not pending_trades:
                    logger.debug("No active algo setups or open trades found.")
                    await asyncio.sleep(60)
                    continue
                
                # IMPORTANT: Run Exits and Invalidations FIRST and wait for them to finish
                # This ensures that if a Single Supertrend flips, the position is closed
                # before we check for the new reverse entry.
                exit_tasks = [self.process_open_trade(trade) for trade in open_trades]
                inv_tasks = [self.process_pending_trade(trade) for trade in pending_trades]
                if exit_tasks or inv_tasks:
                    await asyncio.gather(*(exit_tasks + inv_tasks))
                
                # Now check configs for entries
                entry_tasks = [self.process_algo_setup(setup) for setup in active_setups]
                if entry_tasks:
                    await asyncio.gather(*entry_tasks)
                    
                # Boundary-aligned sleep calculation
                timeframes = [s.get("timeframe", "15m") for s in active_setups] + [t.get("timeframe", "15m") for t in open_trades] + [t.get("timeframe", "15m") for t in pending_trades]
                if not timeframes:
                    await asyncio.sleep(60)
                    continue
                    
                from utils.timeframe import get_timeframe_seconds, get_next_boundary_time
                timeframe_seconds_map = {tf: get_timeframe_seconds(tf) for tf in set(timeframes)}
                shortest_seconds = min(timeframe_seconds_map.values())
                shortest_tf = next(tf for tf in timeframes if timeframe_seconds_map[tf] == shortest_seconds)
                
                now = datetime.utcnow()
                next_boundary = get_next_boundary_time(shortest_tf, now)
                
                sleep_seconds = (next_boundary - now).total_seconds() + 2.0
                logger.info(f"Algo Engine sleeping for {sleep_seconds:.1f} seconds (until next {shortest_tf} boundary)")
                await asyncio.sleep(sleep_seconds)
                
            except Exception as e:
                logger.error(f"Error in algo monitoring loop: {e}")
                await asyncio.sleep(60)

    def _build_cache_data(self, strategy, indicator_result, setup_id, setup_type, setup_name, is_paper, asset, timeframe):
        """Build IndicatorCache document from strategy's get_cache_mapping()."""
        mapping = strategy.get_cache_mapping(indicator_result)
        return {
            "setup_id": setup_id,
            "setup_type": setup_type,
            "setup_name": setup_name,
            "is_paper_trade": is_paper,
            "asset": asset,
            "timeframe": timeframe,
            "current_price": mapping["current_price"],
            "primary_name": mapping.get("primary_name", "Primary"),
            "primary_signal": mapping["primary_signal"],
            "primary_signal_text": mapping["primary_signal_text"],
            "primary_value": mapping["primary_value"],
            "secondary_name": mapping.get("secondary_name", "Secondary"),
            "secondary_signal": mapping["secondary_signal"],
            "secondary_signal_text": mapping["secondary_signal_text"],
            "secondary_value": mapping["secondary_value"],
            "strategy_state": mapping.get("strategy_state", {}),
        }

    async def process_algo_setup(self, algo_setup: Dict[str, Any]):
        start_time = time.time()
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        asset = algo_setup['asset'].upper()
        timeframe = algo_setup['timeframe']
        
        now = datetime.utcnow()
        if not is_at_candle_boundary(timeframe, now):
            return
            
        try:
            api_id = algo_setup['api_id']
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred: return
            
            client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
            
            try:
                strategy = self._get_strategy(setup_id, algo_setup.get('indicator', 'dual_supertrend'), algo_setup.get('indicator_params', {}))
                indicator_result = await strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if not indicator_result:
                    return
                
                # Fetch previous strategy state BEFORE overwriting cache
                previous_state = await get_last_strategy_state(setup_id, asset, timeframe)
                
                # Fetch previous full cache for flip detection (need both primary + secondary signals)
                from database.mongodb import mongodb
                _db = mongodb.get_db()
                prev_cache = await _db.indicator_cache.find_one({
                    "setup_id": setup_id, "asset": asset, "timeframe": timeframe
                })
                
                # Save to Indicator Cache for Dashboard (strategy-agnostic)
                cache_data = self._build_cache_data(
                    strategy, indicator_result, setup_id, "algo", setup_name,
                    algo_setup.get("is_paper_trade", False), asset, timeframe
                )
                await save_indicator_cache(cache_data)
                
                if indicator_result.get("cached"):
                    return
            finally:
                await client.close()
                
            # --- Universal Flip Detection (Telegram alert) ---
            # Compare previous vs current signals and notify on change.
            # Works for every strategy because it reads the generic primary/secondary fields.
            if prev_cache:
                p_name = cache_data.get("primary_name", "Primary")
                s_name = cache_data.get("secondary_name", "Secondary")
                
                for signal_key, name_key, text_key in [
                    ("primary_signal", "primary_name", "primary_signal_text"),
                    ("secondary_signal", "secondary_name", "secondary_signal_text"),
                ]:
                    # Skip secondary if it mirrors primary (e.g. Single ST)
                    if signal_key == "secondary_signal" and p_name == s_name:
                        continue
                    
                    old_sig = prev_cache.get(signal_key)
                    new_sig = cache_data[signal_key]
                    if old_sig is not None and old_sig != new_sig:
                        old_text = "Uptrend" if old_sig == 1 else "Downtrend"
                        new_text = cache_data.get(text_key, "Uptrend" if new_sig == 1 else "Downtrend")
                        flipped_name = cache_data.get(name_key, "Indicator")
                        try:
                            await self.logger_bot.send_indicator_flip(
                                setup_name=setup_name,
                                asset=asset,
                                timeframe=timeframe,
                                indicator_name=flipped_name,
                                old_signal_text=old_text,
                                new_signal_text=new_text,
                                primary_name=p_name,
                                primary_signal=cache_data["primary_signal"],
                                primary_value=cache_data.get("primary_value"),
                                secondary_name=s_name,
                                secondary_signal=cache_data["secondary_signal"],
                                secondary_value=cache_data.get("secondary_value"),
                                current_price=cache_data.get("current_price")
                            )
                        except Exception as e:
                            logger.error(f"Error sending flip notification for {flipped_name}: {e}")
                
            # Generate entry signal using the strategy (strategy-agnostic)
            # Reuse the same cached strategy instance from above — no need to recreate
            entry_signal = strategy.generate_entry_signal(
                setup_id,
                previous_state,
                indicator_result
            )
            
            # If signal exists and there's no open trade for this setup+asset, place order
            if entry_signal:
                # Direction constraint (long_only / short_only)
                setup_direction = algo_setup.get("direction", "both")
                if setup_direction == "long_only" and entry_signal.side != "long":
                    logger.info(f"SKIP entry for {setup_name} - setup is long_only but signal is {entry_signal.side.upper()}")
                    return
                elif setup_direction == "short_only" and entry_signal.side != "short":
                    logger.info(f"SKIP entry for {setup_name} - setup is short_only but signal is {entry_signal.side.upper()}")
                    return
                
                from database.crud import get_open_trade_by_setup, get_pending_trade_by_setup
                # To prevent double entries
                open_trade = await get_open_trade_by_setup(setup_id)
                pending_trade = await get_pending_trade_by_setup(setup_id)
                
                if open_trade or pending_trade:
                    logger.info(f"SKIP entry for {setup_name} - already active trade exists.")
                    return
                    
                # Ensure client is connected for order placement
                client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                try:
                    await self.position_manager.place_breakout_entry_order(
                        client, algo_setup, 
                        entry_side=entry_signal.side,
                        breakout_price=entry_signal.trigger_price,
                        stop_loss_price=entry_signal.stop_loss,
                        immediate=entry_signal.immediate
                    )
                finally:
                    await client.close()
                    
        except Exception as e:
            logger.error(f"Error processing algo setup {setup_name}: {e}")

    async def process_open_trade(self, trade_state: Dict[str, Any]):
        trade_id = str(trade_state['_id'])
        setup_id = trade_state['setup_id']
        asset = trade_state['asset']
        timeframe = trade_state['timeframe']
        current_position = trade_state.get('current_position')
        
        now = datetime.utcnow()
        if not is_at_candle_boundary(timeframe, now):
            return
            
        try:
            # We need the parent config for api keys and rules
            from database.crud import get_algo_setup_by_id, get_screener_setup_by_id
            setup = await get_algo_setup_by_id(setup_id)
            if not setup:
                setup = await get_screener_setup_by_id(setup_id)
            if not setup: return
            
            api_id = setup['api_id']
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred: return
            
            client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
            
            try:
                strategy = self._get_strategy(setup_id, setup.get('indicator', 'dual_supertrend'), setup.get('indicator_params', {}))
                indicator_result = await strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if not indicator_result:
                    await client.close()
                    return
                
                # Save to Indicator Cache for Dashboard (strategy-agnostic)
                # IMPORTANT: Preserve existing strategy_state so that
                # process_algo_setup can still detect the flip for reverse entry.
                # Only process_algo_setup is allowed to overwrite strategy_state.
                cache_data = self._build_cache_data(
                    strategy, indicator_result, setup_id,
                    trade_state.get("setup_type", "algo"),
                    trade_state.get("setup_name", "Unknown"),
                    trade_state.get("is_paper_trade", False),
                    asset, timeframe
                )
                existing_state = await get_last_strategy_state(setup_id, asset, timeframe)
                if existing_state is not None:
                    cache_data["strategy_state"] = existing_state
                await save_indicator_cache(cache_data)
            except Exception as e:
                logger.error(f"Error calculating indicators for {asset}: {e}")
                await client.close()
                return
                        
            # Exit Check (strategy-agnostic)
            exit_signal = strategy.generate_exit_signal(
                setup_id, current_position, indicator_result
            )
            
            if exit_signal:
                logger.info(
                    f"EXIT SIGNAL for {asset}: {exit_signal.reason} "
                    f"(final indicator value: {exit_signal.stop_loss})"
                )
                
                # Persist final indicator value before closing the trade
                await update_trade_state(trade_id, {
                    "pending_sl_price": exit_signal.stop_loss
                })
                
                success, exit_price, _ = await self.position_manager.execute_exit(
                    client, trade_state, exit_signal.reason
                )
                
                if success:
                    try:
                        entry_price = trade_state.get("entry_price")
                        lot_size = trade_state.get("lot_size", 0)
                        pnl = None
                        pnl_inr = None
                        if entry_price and exit_price:
                            if current_position == "long":
                                pnl = (exit_price - entry_price) * lot_size
                            else:
                                pnl = (entry_price - exit_price) * lot_size
                            from config.settings import settings as app_settings
                            pnl_inr = pnl * app_settings.usd_to_inr_rate
                        
                        await self.logger_bot.send_trade_exit_detail(
                            setup_name=trade_state.get("setup_name", "Unknown"),
                            asset=asset,
                            timeframe=timeframe,
                            direction=current_position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            lot_size=lot_size,
                            pnl_usd=pnl,
                            pnl_inr=pnl_inr,
                            exit_signal_text=exit_signal.reason,
                            exit_reason=exit_signal.reason
                        )
                    except Exception as e:
                        logger.error(f"Failed to send exit notification for {asset}: {e}")
                
            await client.close()
            
        except Exception as e:
            logger.error(f"Error processing open trade {trade_id}: {e}")

    async def process_pending_trade(self, trade_state: Dict[str, Any]):
        trade_id = str(trade_state['_id'])
        setup_id = trade_state['setup_id']
        asset = trade_state['asset']
        timeframe = trade_state['timeframe']
        pending_side = trade_state.get('pending_entry_side')
        
        now = datetime.utcnow()
        from utils.timeframe import is_at_candle_boundary
        if not is_at_candle_boundary(timeframe, now):
            return
            
        try:
            from database.crud import get_algo_setup_by_id, get_screener_setup_by_id
            setup = await get_algo_setup_by_id(setup_id)
            if not setup:
                setup = await get_screener_setup_by_id(setup_id)
            if not setup: return
            
            api_id = setup['api_id']
            from database.crud import get_api_credential_by_id
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred: return
            
            from api.delta_client import DeltaExchangeClient
            client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
            
            try:
                strategy = self._get_strategy(setup_id, setup.get('indicator', 'dual_supertrend'), setup.get('indicator_params', {}))
                indicator_result = await strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if not indicator_result:
                    return
                
                # Save to Indicator Cache for Dashboard (strategy-agnostic)
                # IMPORTANT: Preserve existing strategy_state (same reason as process_open_trade)
                cache_data = self._build_cache_data(
                    strategy, indicator_result, setup_id,
                    trade_state.get("setup_type", "algo"),
                    trade_state.get("setup_name", "Unknown"),
                    trade_state.get("is_paper_trade", False),
                    asset, timeframe
                )
                existing_state = await get_last_strategy_state(setup_id, asset, timeframe)
                if existing_state is not None:
                    cache_data["strategy_state"] = existing_state
                await save_indicator_cache(cache_data)
                
            except Exception as e:
                logger.error(f"Error calculating indicators for pending {asset}: {e}")
                await client.close()
                return
                
            # Invalidation Check (strategy-agnostic)
            is_invalidated = strategy.should_invalidate_pending_entry(pending_side, indicator_result)
                
            if is_invalidated:
                logger.info(f"[INVALIDATION] Strategy invalidated pending {pending_side.upper()} for {asset}. Cancelling entry.")
                
                if trade_state.get("is_paper_trade", False):
                    from strategy.paper_trader import paper_trader
                    await paper_trader.cancel_pending_entry(trade_id)
                else:
                    pending_order_id = trade_state.get("pending_entry_order_id")
                    product_id = trade_state.get("product_id")
                    if pending_order_id and product_id:
                        from api.orders import cancel_order
                        await cancel_order(client, product_id, pending_order_id)
                        
                    from database.crud import update_trade_state, get_db, release_position_lock
                    await update_trade_state(trade_id, {
                        "status": "cancelled",
                        "pending_entry_order_id": None
                    })
                    db = await get_db()
                    await release_position_lock(db, asset, setup_id)
                    
            await client.close()
            
        except Exception as e:
            logger.error(f"Error processing pending trade {trade_id}: {e}")


    async def monitor_pending_entries(self, poll_interval=3):
        """
        Polls all pending stop-market entries every few seconds and attaches stop-loss if filled.
        """
        logger.info("Starting fast fill-monitor for pending entries.")
        while True:
            try:
                from database.crud import get_pending_entry_trade_states, get_algo_setup_by_id, get_screener_setup_by_id, get_api_credential_by_id
                
                pending_trades = await get_pending_entry_trade_states()
                for trade in pending_trades:
                    if trade.get("is_paper_trade"):
                        continue
                        
                    setup_id = trade['setup_id']
                    setup = await get_algo_setup_by_id(setup_id) or await get_screener_setup_by_id(setup_id)
                    if not setup: continue
                    
                    api_id = setup['api_id']
                    cred = await get_api_credential_by_id(api_id, decrypt=True)
                    if not cred: continue
                    
                    client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                    try:
                        await self.position_manager.check_entry_order_filled(client, trade, None, logger_bot=self.logger_bot)
                    finally:
                        await client.close()
                        
                await asyncio.sleep(poll_interval)
            except Exception as e:
                logger.error(f"[FILL-MONITOR] Error: {e}")
                await asyncio.sleep(poll_interval)

    async def monitor_paper_trades(self, poll_interval=5):
        """
        Background loop to monitor virtual stop-losses and pending entries for paper trades.
        """
        logger.info("[PAPER] Starting paper trade price monitor...")
        
        while True:
            try:
                from database.crud import get_open_trade_states, get_pending_entry_trade_states, get_all_active_algo_setups, get_all_active_screener_setups, get_api_credential_by_id
                
                open_trades = [t for t in await get_open_trade_states() if t.get("is_paper_trade")]
                pending_trades = [t for t in await get_pending_entry_trade_states() if t.get("is_paper_trade")]
                
                if not open_trades and not pending_trades:
                    await asyncio.sleep(poll_interval * 2)
                    continue
                    
                client = None
                all_configs = await get_all_active_algo_setups() + await get_all_active_screener_setups()
                
                for config in all_configs:
                    api_id = config.get("api_id")
                    if api_id:
                        cred = await get_api_credential_by_id(api_id, decrypt=True)
                        if cred:
                            client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                            break
                            
                if not client:
                    await asyncio.sleep(poll_interval)
                    continue
                    
                try:
                    await paper_trader.check_pending_entries(client)
                    await paper_trader.check_stop_losses(client)
                finally:
                    await client.close()
                    
                await asyncio.sleep(poll_interval)
                
            except Exception as e:
                logger.error(f"[PAPER] Error in paper trade monitor: {e}")
                await asyncio.sleep(poll_interval)
