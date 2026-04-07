import logging
import asyncio
import time
from typing import Dict, Any, Optional
from datetime import datetime
from database.crud import (
    get_all_active_screener_setups,
    get_open_trade_states,
    get_pending_entry_trade_states,
    get_trade_state_by_id,
    update_trade_state,
    get_all_active_algo_setups, get_api_credential_by_id,
    update_algo_setup, save_indicator_cache, get_indicator_cache,
    get_algo_setup_by_id
)
from api.delta_client import DeltaExchangeClient
from api.orders import is_order_gone, cancel_order  # CRITICAL: import robust methods
from strategy.dual_supertrend import DualSuperTrendStrategy
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
    """Main trading engine for executing algo strategies - ENHANCED WITH DYNAMIC SLEEP."""

    def __init__(self, logger_bot: LoggerBot):
        self.strategy = DualSuperTrendStrategy()
        self.position_manager = PositionManager()
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


    async def run_continuous_monitoring(self):
        """
        Background loop to monitor active setups and open trades on candle boundaries.
        """
        logger.info("🚀 Starting boundary-aligned algo monitoring loop.")
        
        while True:
            try:
                active_setups = await get_all_active_algo_setups()
                open_trades = await get_open_trade_states()
                
                if not active_setups and not open_trades:
                    logger.debug("ℹ️ No active algo setups or open trades found.")
                    import asyncio
                    await asyncio.sleep(60)
                    continue
                
                # Check configs for entries
                import asyncio
                for setup in active_setups:
                    asyncio.create_task(self.process_algo_setup(setup))
                    
                # Check open trades for trailing SL / exits
                for trade in open_trades:
                    asyncio.create_task(self.process_open_trade(trade))
                    
                # Boundary-aligned sleep calculation
                timeframes = [s.get("timeframe", "15m") for s in active_setups] + [t.get("timeframe", "15m") for t in open_trades]
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
                logger.info(f"⏳ Algo Engine sleeping for {sleep_seconds:.1f} seconds (until next {shortest_tf} boundary)")
                await asyncio.sleep(sleep_seconds)
                
            except Exception as e:
                logger.error(f"❌ Error in algo monitoring loop: {e}")
                import asyncio
                await asyncio.sleep(60)

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
                indicator_result = await self.strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if not indicator_result or indicator_result.get("cached"):
                    return
            finally:
                await client.close()
                
            entry_signal = self.strategy.generate_entry_signal(
                setup_id,
                None, # last_perusu_signal no longer needed for pure breakout
                indicator_result
            )
            
            # If signal exists and there's no open trade for this setup+asset, place order
            if entry_signal:
                from database.crud import get_open_trade_by_setup, get_pending_trade_by_setup
                # To prevent double entries
                open_trade = await get_open_trade_by_setup(setup_id)
                pending_trade = await get_pending_trade_by_setup(setup_id)
                
                if open_trade or pending_trade:
                    logger.info(f"⏭️ SKIP entry for {setup_name} - already active trade exists.")
                    return
                    
                sirusu_value = indicator_result.get('sirusu', {}).get('supertrend_value', 0)
                # Ensure client is connected for order placement
                client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                try:
                    await self.position_manager.place_breakout_entry_order(
                        client, algo_setup, entry_signal,
                        indicator_result['perusu']['supertrend_value'],
                        sirusu_value, immediate=False
                    )
                finally:
                    await client.close()
                    
        except Exception as e:
            logger.error(f"❌ Error processing algo setup {setup_name}: {e}")

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
                indicator_result = await self.strategy.calculate_indicators(
                    client, asset, timeframe
                )
                if not indicator_result or indicator_result.get("cached"):
                    # For exiting/trailing SL we must process it even if cached, but calculation will just return cached values
                    pass
            except Exception as e:
                logger.error(f"❌ Error calculating indicators for {asset}: {e}")
                await client.close()
                return
                
            sirusu_data = indicator_result.get('sirusu')
            if not sirusu_data:
                await client.close()
                return
                
            # Trailing SL
            new_sirusu_value = sirusu_data.get('supertrend_value')
            if new_sirusu_value and new_sirusu_value != trade_state.get("pending_sl_price"):
                from database.crud import update_trade_state
                await update_trade_state(trade_id, {"pending_sl_price": new_sirusu_value})
                if trade_state.get("is_paper_trade"):
                    await paper_trader.update_stop_loss(trade_id, new_sirusu_value)
                else:
                    sl_order_id = trade_state.get("stop_loss_order_id")
                    if sl_order_id:
                        product_id = trade_state.get("product_id")
                        await self.position_manager._place_stop_loss_protection(
                            client, product_id, trade_state["lot_size"], current_position,
                            new_sirusu_value, setup_id, asset, trade_state["user_id"],
                            existing_order_id=sl_order_id
                        )
                        
            # Exit Check
            exit_signal = self.strategy.generate_exit_signal(
                setup_id, current_position, indicator_result
            )
            
            if exit_signal:
                await self.position_manager.execute_exit(
                    client, trade_state, f"Sirusu flip to {indicator_result['sirusu']['signal_text']}"
                )
                
            await client.close()
            
        except Exception as e:
            logger.error(f"❌ Error processing open trade {trade_id}: {e}")

    async def monitor_pending_entries(self, poll_interval=3):
        """
        Polls all pending stop-market entries every few seconds and attaches stop-loss if filled.
        """
        logger.info("🚦 Starting fast fill-monitor for pending entries.")
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
                        await self.position_manager.check_entry_order_filled(client, trade, None)
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
