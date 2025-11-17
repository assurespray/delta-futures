import logging
import asyncio
from datetime import datetime
from database.crud import (
    get_all_active_algo_setups,
    get_api_credential_by_id,
    update_algo_setup,
    create_position_lock,
    delete_position_lock,
    get_db, 
    acquire_position_lock, 
    get_position_lock
)
from api.delta_client import DeltaExchangeClient
from api.positions import get_position_by_symbol
from api.orders import get_open_orders, place_stop_loss_order
from strategy.dual_supertrend import DualSuperTrendStrategy
from strategy.position_manager import PositionManager
from utils.timeframe import get_next_boundary_time
from services.logger_bot import LoggerBot

logger = logging.getLogger(__name__)

async def startup_reconciliation(logger_bot: LoggerBot):
    # Step 1: Remove old locks
    await delete_position_lock() # assuming this is a 'clear all' for startup
    logger.info("✅ Old stale position locks deleted")

    strategy = DualSuperTrendStrategy()
    position_manager = PositionManager()
    setups = await get_all_active_algo_setups()

    for setup in setups:
        api_id = setup.get("api_id")
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred:
            logger.warning(f"Could not load credentials for api_id {api_id}")
            continue
        client = DeltaExchangeClient(cred['api_key'], cred['api_secret'])
        try:
            symbol = setup.get("asset")
            product_id = setup.get("product_id")
            lot_size = setup.get("lot_size")
            timeframe = setup.get("timeframe")
            setup_id = str(setup["_id"])
            addl_prot = setup.get("additional_protection", False)

            # Step 2: Fetch position & create lock
            position = await get_position_by_symbol(client, symbol)
            position_size = position.get("size", 0) if position else 0
            if position_size == 0:
                await update_algo_setup(setup_id, {
                    "current_position": None,
                    "last_entry_price": None,
                    "position_lock_acquired": False,
                    "stop_loss_order_id": None,
                })
                continue

            # --- PLACE THIS BLOCK HERE ---
            db = await get_db()
            lock_acquired = await acquire_position_lock(db, symbol, setup_id, setup.get("setup_name"))
            if not lock_acquired:
                lock = await get_position_lock(db, symbol)
                logger.error(f"Reconciliation: {symbol} locked by {lock['setup_id']} ({lock.get('setup_name')}) already.")
            # --- END PLACEMENT ---

            position_side = "long" if position_size > 0 else "short"

            # Step 3: Get SL details if present
            stop_loss_order_id = None
            open_orders = await get_open_orders(client, product_id)
            for order in open_orders or []:
                if order.get("state") in ("open", "untriggered") and \
                   order.get("order_type") in ("stop_market_order", "stop_market") and \
                   order.get("reduce_only"):
                    stop_loss_order_id = order.get("id")
                    break

            # Step 4: Wait for next boundary if needed
            now = datetime.utcnow()
            time_until_boundary = (get_next_boundary_time(timeframe, now) - now).total_seconds()
            if time_until_boundary < 30:
                await asyncio.sleep(time_until_boundary + 1)

            # Step 5: Calculate Perusu & Sirusu
            indicator_result = await strategy.calculate_indicators(client, symbol, timeframe)
            if not indicator_result:
                logger.error(f"Indicator calculation failed for {symbol}")
                continue

            perusu_signal, sirusu_signal = indicator_result["perusu"]["signal"], indicator_result["sirusu"]["signal"]
            perusu_text, sirusu_text = indicator_result["perusu"]["signal_text"], indicator_result["sirusu"]["signal_text"]
            sirusu_value = indicator_result["sirusu"]["supertrend_value"]

            # Step 6: Check for flip/exit logic
            if position_side == "long":
                valid = perusu_signal == 1 and sirusu_signal == 1
            else:
                valid = perusu_signal == -1 and sirusu_signal == -1

            if not valid:
                await logger_bot.send_info(f"Sirusu flip detected for {symbol} ({position_side}), exiting at market!")
                exit_success = await position_manager.execute_exit(
                    client=client,
                    algo_setup=setup,
                    sirusu_signal_text=sirusu_text
                )
                if exit_success:
                    await delete_position_lock(symbol)
                continue

            # Step 7: SL placement as needed
            if addl_prot and not stop_loss_order_id:
                sl_order = await place_stop_loss_order(
                    client, product_id, abs(position_size),
                    'sell' if position_side == 'long' else 'buy',
                    sirusu_value, True
                )
                stop_loss_order_id = sl_order.get("id") if sl_order else None

            # Step 8: Save DB state
            await update_algo_setup(setup_id, {
                "current_position": position_side,
                "last_entry_price": position.get("entry_price"),
                "position_lock_acquired": True,
                "stop_loss_order_id": stop_loss_order_id,
                "last_signal_time": datetime.utcnow(),
            })

            await logger_bot.send_info(
                f"{symbol} ({position_side.upper()}) sync: "
                f"Perusu {perusu_text}, Sirusu {sirusu_text} SL:{'Y' if stop_loss_order_id else 'N'}"
            )

        except Exception as e:
            logger.error(f"[{symbol}] Reconciliation failed: {e}")
            await logger_bot.send_error(f"[{symbol}] reconciliation error: {str(e)}")
        finally:
            await client.close()

    logger.info("✅ Startup reconciliation complete")
  
