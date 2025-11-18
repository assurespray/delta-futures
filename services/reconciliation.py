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
            setup_name = setup.get("setup_name")
            addl_prot = setup.get("additional_protection", False)

            position = await get_position_by_symbol(client, symbol)

            position_size = position.get("size", 0) if position else 0
            if position_size == 0:
                await update_algo_setup(setup_id, {
                    "current_position": None,
                    "last_entry_price": None,
                    "position_lock_acquired": False,
                    "stop_loss_order_id": None,
                })
                await client.close()
                continue

            db = await get_db()
            # Always create a FRESH lock after startup lock cleanup
            lock_acquired = await acquire_position_lock(db, symbol, setup_id, setup_name)
            if lock_acquired:
                logger.info(f"Startup: Created new position lock for {symbol} ({setup_name})")
            else:
                logger.error(f"Startup: Failed to create lock for {symbol}. This should not happen after cleanup!")
                continue  # Only move on to next setup if you can't lock

            position_side = "long" if position_size > 0 else "short"

            # Step 4: Get SL details if present
            stop_loss_order_id = None
            open_orders = await get_open_orders(client, product_id)
            logger.info(f"Open orders for {symbol}: {open_orders}")

            for order in open_orders or []:
                # Normalize and robustly check order fields
                state = (order.get("state") or "").lower()
                order_type = (order.get("order_type") or "").lower()
                reduce_only = order.get("reduce_only", False)
                # Some exchanges use 'stop_market_order', some use 'stop_market', some may use 'stop_loss_order'
                # Accept any stop order that is reduce_only and open/untriggered
                if state in ("open", "untriggered") and \
                    (
                        "stop" in order_type or
                        order_type in ("stop_loss_order", "stop_market_order", "stop_market")
                    ) and \
                    reduce_only:
                    stop_loss_order_id = order.get("id")
                    logger.info(f"Detected existing stop-loss order for {symbol}: {stop_loss_order_id}")
                    break

            now = datetime.utcnow()
            time_until_boundary = (get_next_boundary_time(timeframe, now) - now).total_seconds()
            if time_until_boundary < 30:
                await asyncio.sleep(time_until_boundary + 1)

            indicator_result = await strategy.calculate_indicators(client, symbol, timeframe)
            if not indicator_result:
                logger.error(f"Indicator calculation failed for {symbol}")
                continue

            perusu_signal, sirusu_signal = indicator_result["perusu"]["signal"], indicator_result["sirusu"]["signal"]
            perusu_text, sirusu_text = indicator_result["perusu"]["signal_text"], indicator_result["sirusu"]["signal_text"]
            sirusu_value = indicator_result["sirusu"]["supertrend_value"]

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

            if addl_prot and not stop_loss_order_id:
                sl_order = await place_stop_loss_order(
                    client, product_id, abs(position_size),
                    'sell' if position_side == 'long' else 'buy',
                    sirusu_value, True
                )
                stop_loss_order_id = sl_order.get("id") if sl_order else None

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
    
