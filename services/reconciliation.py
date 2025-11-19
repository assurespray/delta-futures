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

    # ==== CRITICAL: WIPE ALL LOCKS BEFORE STARTUP ====
    db = await get_db()
    delete_result = await db["position_locks"].delete_many({})
    logger.info(f"Startup: Deleted all position locks (count={delete_result.deleted_count}) before reconciliation")
    # ==================================================

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

            # Check if no position exists
            if position_size == 0:
                logger.info(f"No open position for {symbol}, cleaning up setup")
                await update_algo_setup(setup_id, {
                    "current_position": None,
                    "last_entry_price": None,
                    "position_lock_acquired": False,
                    "stop_loss_order_id": None,
                })
                await client.close()
                continue

            if position and position.get("product_id") == product_id:
                setup["current_position"] = "long" if position["size"] > 0 else "short"
                setup["last_entry_price"] = position.get("entry_price")
                setup["product_id"] = position.get("product_id")
                setup["position_obj"] = position  # optional, for easy access anywhere

                logger.info(
                    f"[SYNC] symbol={symbol} product_id={product_id} "
                    f"size={position['size']} current_position={setup['current_position']} "
                    f"position_obj={setup['position_obj']}"
                )
            else:
                logger.warning(f"No matching position for {symbol} (wanted product_id={product_id} got {position.get('product_id') if position else None})")
                setup["current_position"] = None
                setup["last_entry_price"] = None
                setup["product_id"] = product_id
                setup["position_obj"] = None

                logger.warning(
                    f"[SYNC] symbol={symbol} - NO OPEN POSITION (wanted product_id={product_id}, got {position.get('product_id') if position else None})"
                )
                # optionally update algo setup DB here as well
                await update_algo_setup(setup_id, setup)
                await client.close()
                continue

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
            locks = await db["position_locks"].find().to_list(length=100)
            logger.error(f"[DEBUG] Current locks after reconciliation: {locks}")
            if not lock_acquired:
                logger.error(f"❌ RECONCILIATION: Could not acquire lock for {symbol} by {setup_id}")
            else:
                logger.info(f"✅ RECONCILIATION: Lock acquired for {symbol} by {setup_id}")
            if lock_acquired:
                logger.info(f"Startup: Created new position lock for {symbol} ({setup_name})")
            else:
                existing_lock = await get_position_lock(db, symbol)
                logger.debug(f"[DEBUG] Lock check for {symbol}: {existing_lock}")
                logger.error(
                    f"Startup: Failed to create lock for {symbol}. "
                    f"Existing lock: {existing_lock} | Requested by setup_id={setup_id}, setup_name={setup_name}"
                )
                continue  # Only move on to next setup if you can't lock

            position_side = "long" if position_size > 0 else "short"

            # Step 4: Get SL details if present
            stop_loss_order_id = None
            open_orders = await get_open_orders(client, product_id)
            logger.info(f"Open orders for {symbol}: {open_orders}")

            for order in open_orders or []:
                state = (order.get("state") or "").lower()
                stop_order_type = (order.get("stop_order_type") or "").lower()
                reduce_only = order.get("reduce_only", False)
                product_id_in_order = order.get("product_id")
                # Defensive debug log
                logger.debug(
                    f"Order check: id={order.get('id')}, state={state}, stop_type={stop_order_type}, "
                    f"reduce_only={reduce_only}, product_id={product_id_in_order}, "
                    f"wanted={product_id}, symbol={order.get('product_symbol')}"
                )
                if (
                    state in ("pending", "open", "untriggered")
                    and stop_order_type == "stop_loss_order"
                    and reduce_only
                    and product_id_in_order == product_id  # STRICT: Only process for correct symbol/product
                ):
                    stop_loss_order_id = order.get("id")
                    logger.info(
                        f"Detected existing stop-loss order for {symbol}: {stop_loss_order_id} "
                        f"(product_id={product_id_in_order}, symbol={order.get('product_symbol')})"
                    )            
                    break

            now = datetime.utcnow()
            time_until_boundary = (get_next_boundary_time(timeframe, now) - now).total_seconds()
            if time_until_boundary < 30:
                await asyncio.sleep(time_until_boundary + 1)

            # [Optional] Log or fetch candles here if you have direct access, e.g.:
            # candles = await fetch_candles(client, symbol, timeframe)
            # logger.info(f"{symbol}: Candle count for indicator calc = {len(candles)}, first candle: {candles[0] if candles else 'N/A'}")

            logger.info(f"Calling indicator calculation for {symbol}, timeframe={timeframe}")

            try:
                indicator_result = await strategy.calculate_indicators(client, symbol, timeframe, skip_boundary_check=True)
                if not indicator_result:
                    logger.error(f"Indicator calculation failed for {symbol} (likely cause: not enough candles, bad input data, or server error!)")
                    continue
                logger.info(f"Indicator calculation SUCCESS for {symbol}: {indicator_result}")
            except Exception as e:
                logger.error(f"Exception in indicator calculation for {symbol}: {e}")
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

                logger.info(f"Passing to manager: symbol={symbol}, setup product_id={setup['product_id']}, position={setup.get('position_obj')}")
                exit_success = await position_manager.execute_exit(
                    client=client,
                    algo_setup=setup,
                    sirusu_signal_text=sirusu_text
                )
                if exit_success:
                    await delete_position_lock(symbol)
                    logger.info(f"Deleted position lock for {symbol}")
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

def filter_orders_by_symbol_and_product_id(
    orders: list,
    target_symbol: str,
    target_product_id: int
) -> list:
    """
    Returns only those orders matching the given symbol and product_id.
    Works for both top-level and nested product information.

    :param orders: List of order dicts from exchange
    :param target_symbol: Symbol to match (e.g., 'ADAUSD')
    :param target_product_id: Integer product_id (from exchange metadata)
    :return: List of filtered order dicts
    """
    filtered = []
    for order in orders:
        product_id_matches = order.get("product_id") == target_product_id
        top_symbol = order.get("product_symbol")
        nested_symbol = order.get("product", {}).get("symbol")
        # Accept match if product_id matches and symbol matches (from either field)
        if product_id_matches and (top_symbol == target_symbol or nested_symbol == target_symbol):
            filtered.append(order)
    return filtered
