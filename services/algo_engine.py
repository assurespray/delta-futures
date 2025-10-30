"""Core trading engine for algo execution."""
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from database.crud import (
    get_all_active_algo_setups, get_api_credential_by_id,
    update_algo_setup, upsert_indicator_cache, get_indicator_cache
)
from api.delta_client import DeltaExchangeClient
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.position_manager import PositionManager
from services.logger_bot import LoggerBot
from utils.timeframe import (
    is_at_candle_boundary,
    get_next_boundary_time,
    get_timeframe_display_name
)

logger = logging.getLogger(__name__)


class AlgoEngine:
    """Main trading engine for executing algo strategies."""
    
    def __init__(self, logger_bot: LoggerBot):
        """
        Initialize algo engine.
        
        Args:
            logger_bot: Logger bot instance for notifications
        """
        self.strategy = DualSuperTrendStrategy()
        self.position_manager = PositionManager()
        self.logger_bot = logger_bot
        self.running_tasks = {}
    
    async def process_algo_setup(self, algo_setup: Dict[str, Any]):
        """
        Process a single algo setup - calculate indicators and execute trades.
    
    Args:
            algo_setup: Algo setup configuration
        """
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        asset = algo_setup['asset']
        timeframe = algo_setup['timeframe']
        direction = algo_setup['direction']
        current_position = algo_setup.get('current_position')
    
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
    
        # Log that we're at a boundary
        tf_display = get_timeframe_display_name(timeframe)
        logger.info(f"✅ [{setup_name}] At {tf_display} boundary - Processing {asset}")
    
        try:
            # Get API credentials
            api_id = algo_setup['api_id']
            cred = await get_api_credential_by_id(api_id, decrypt=True)
        
            if not cred:
                logger.error(f"❌ Failed to load credentials for setup {setup_name}")
                await self.logger_bot.send_error(
                    f"Failed to load API credentials for setup: {setup_name}"
                )
                return
        
            # Create Delta Exchange client
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
        
            # Calculate indicators
            logger.info(f"🔄 Processing {setup_name} ({asset} {timeframe})")
            indicator_result = await self.strategy.calculate_indicators(client, asset, timeframe)
        
            if not indicator_result:
                logger.warning(f"⚠️ Failed to calculate indicators for {setup_name}")
                await client.close()
                return
        
            perusu_data = indicator_result['perusu']
            sirusu_data = indicator_result['sirusu']
        
            # ✅ CHECK SIGNALS BEFORE CACHING
        
            # Check for entry signal (only if not in position)
            if not current_position:
                # Get last Perusu signal from cache BEFORE updating
                cached_perusu = await get_indicator_cache(setup_id, "perusu")
                last_perusu_signal = cached_perusu.get('last_signal') if cached_perusu else None
            
                entry_signal = self.strategy.generate_entry_signal(
                    setup_id,
                    last_perusu_signal,
                    indicator_result
                )
            
                if entry_signal:
                    logger.info(f"🚀 Entry signal detected for {setup_name}: {entry_signal['side'].upper()}")
                
                    # Execute entry
                    success = await self.position_manager.execute_entry(
                        client=client,
                        algo_setup=algo_setup,
                        entry_side=entry_signal['side'],
                        sirusu_value=sirusu_data['supertrend_value']
                    )
                
                    if success:
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
                        await self.logger_bot.send_error(
                            f"Failed to execute entry for {setup_name}"
                        )
            
            # Check for exit signal (only if in position)
            elif current_position:
                exit_signal = self.strategy.generate_exit_signal(
                    setup_id,
                    current_position,
                    indicator_result
                )
            
                if exit_signal:
                    logger.info(f"🚪 Exit signal detected for {setup_name}")
                
                    # Execute exit
                    success = await self.position_manager.execute_exit(
                        client=client,
                        algo_setup=algo_setup,
                        sirusu_signal_text=sirusu_data['signal_text']
                    )
                
                    if success:
                        await self.logger_bot.send_trade_exit(
                            setup_name=setup_name,
                            asset=asset,
                            direction=current_position,
                            sirusu_signal=sirusu_data['signal_text']
                        )
                    else:
                        await self.logger_bot.send_error(
                        f"Failed to execute exit for {setup_name}"
                        )
        
            # ✅ Cache indicator values AFTER signal detection
            await self._cache_indicators(setup_id, perusu_data, sirusu_data, asset, timeframe)
        
            await client.close()
        
        except Exception as e:
            logger.error(f"❌ Exception processing algo setup {setup_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await self.logger_bot.send_error(
                f"Exception in {setup_name}: {str(e)[:200]}"
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
            
        except Exception as e:
            logger.error(f"❌ Failed to cache indicators: {e}")
    
    async def run_continuous_monitoring(self):
        """
        Run continuous monitoring loop for all active algo setups.
        This is the main 24/7 trading loop.
        """
        logger.info("🚀 Starting continuous algo monitoring...")
        await self.logger_bot.send_info("🚀 Algo Engine Started - Monitoring active setups")
        
        while True:
            try:
                # Get all active algo setups
                active_setups = await get_all_active_algo_setups()
                
                if not active_setups:
                    logger.debug("ℹ️ No active algo setups found")
                    await asyncio.sleep(60)  # Check again in 1 minute
                    continue
                
                logger.debug(f"📊 Checking {len(active_setups)} active algo setup(s)")
                
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
                
                # Sleep for 60 seconds before next cycle
                # Boundary checks ensure we only trade at proper candle closes
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ Exception in continuous monitoring: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await self.logger_bot.send_error(f"Monitoring loop error: {str(e)[:200]}")
                await asyncio.sleep(60)  # Wait before retrying
            
