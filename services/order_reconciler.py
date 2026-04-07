import logging
from datetime import datetime
from database.crud import (
    get_api_credential_by_id, get_algo_setup_by_id, get_all_active_algo_setups,
    update_algo_setup, update_algo_activity, get_open_activity_by_setup,
    release_position_lock, get_db
)
from database.mongodb import mongodb
from api.delta_client import DeltaExchangeClient
from api.orders import get_order_status_by_id, get_order_history
from strategy.position_manager import PositionManager
from strategy.paper_trader import is_paper_trade
from config.settings import settings

logger = logging.getLogger(__name__)


async def _handle_sl_fill(order, setup, client, logger_bot=None):
    """
    Handle a stop-loss order that has been filled on the exchange.
    Calculates PnL, closes AlgoActivity, sends Telegram notification,
    closes position records, and releases the position lock.
    """
    setup_id = str(setup["_id"])
    setup_name = setup.get("setup_name", "Unknown")
    symbol = setup.get("asset", "")
    current_position = setup.get("current_position")
    lot_size = setup.get("lot_size", 0)
    product_id = order.get("product_id") or setup.get("product_id")
    order_id = order.get("order_id")

    logger.info(
        f"🛡️ SL FILL DETECTED for {setup_name} ({symbol}) — "
        f"order_id={order_id}, position={current_position}"
    )

    # 1. Find the SL fill price from exchange order history
    exit_price = None
    try:
        if product_id:
            history = await get_order_history(client, product_id)
            if history:
                sl_order = next(
                    (o for o in history if str(o.get("id")) == str(order_id)),
                    None
                )
                if sl_order:
                    raw_fill = sl_order.get("average_fill_price")
                    if raw_fill is not None:
                        exit_price = float(raw_fill)
                        logger.info(f"   SL fill price from history: ${exit_price}")
    except Exception as e:
        logger.warning(f"⚠️ Could not fetch SL fill price from history: {e}")

    # 2. Close the AlgoActivity with PnL
    activity = await get_open_activity_by_setup(setup_id)
    pnl = None
    pnl_inr = None
    entry_price = None
    if activity:
        entry_price = activity.get("entry_price", 0)
        update_data = {
            "exit_time": datetime.utcnow(),
            "exit_price": exit_price,
            "sirusu_exit_signal": "Stop-loss triggered",
            "is_closed": True
        }
        if exit_price and entry_price:
            try:
                ep = float(entry_price)
                xp = float(exit_price)
                if current_position == "long":
                    pnl = (xp - ep) * lot_size
                else:
                    pnl = (ep - xp) * lot_size
                pnl_inr = pnl * settings.usd_to_inr_rate
                update_data["pnl"] = round(pnl, 4)
                update_data["pnl_inr"] = round(pnl_inr, 2)
                logger.info(f"   PnL calculated: ${pnl:.4f} (₹{pnl_inr:.2f})")
            except Exception as e:
                logger.warning(f"⚠️ PnL calculation error: {e}")

        await update_algo_activity(str(activity["_id"]), update_data)
        logger.info(f"✅ AlgoActivity closed for {setup_name}")
    else:
        logger.warning(f"⚠️ No open AlgoActivity found for setup {setup_id}")

    # 3. Clean up algo_setup — clear all position/order state
    await update_algo_setup(setup_id, {
        "current_position": None,
        "last_entry_price": None,
        "stop_loss_order_id": None,
        "pending_entry_order_id": None,
        "pending_entry_side": None,
        "pending_entry_direction_signal": None,
        "entry_trigger_price": None,
        "pending_sl_price": None,
        "last_entry_order_id": None,
        "position_lock_acquired": False
    })
    logger.info(f"✅ Algo setup cleaned for {setup_name}")

    # 4. Close position records in DB
    try:
        db = await get_db()
        await db.positions.update_many(
            {"algo_setup_id": setup_id, "status": "open"},
            {"$set": {"closed_at": datetime.utcnow(), "status": "closed"}}
        )
        await release_position_lock(db, symbol, setup_id)
        logger.info(f"✅ Position records closed and lock released for {symbol}")
    except Exception as e:
        logger.error(f"❌ Error closing position records: {e}")

    # 5. Send Telegram notifications
    if logger_bot:
        try:
            # Log channel notification — build message safely
            entry_price_str = f"${float(entry_price):.5f}" if entry_price else "N/A"
            exit_price_str = f"${exit_price:.5f}" if exit_price else "N/A"
            pnl_str = f"${pnl:.4f}" + (f" (₹{pnl_inr:.2f})" if pnl_inr is not None else "") if pnl is not None else "N/A"

            log_msg = (
                f"🛡️ **STOP-LOSS TRIGGERED**\n\n"
                f"**Setup:** {setup_name}\n"
                f"**Asset:** {symbol}\n"
                f"**Direction:** {current_position.upper() if current_position else 'N/A'}\n"
                f"**SL Order ID:** {order_id}\n"
                f"**Entry Price:** {entry_price_str}\n"
                f"**Exit Price:** {exit_price_str}\n"
                f"**PnL:** {pnl_str}\n\n"
                f"_Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}_"
            )
            await logger_bot.send_message(log_msg)
        except Exception as e:
            logger.warning(f"⚠️ Failed to send SL log notification: {e}")

        try:
            # User-facing notification via main bot
            timeframe = setup.get("timeframe", "")
            pnl_emoji = "💰" if (pnl is not None and pnl >= 0) else "📉"

            user_msg = (
                f"🛡️ **STOP-LOSS EXIT**\n\n"
                f"**Setup:** {setup_name}\n"
                f"**Asset:** {symbol} @ {timeframe}\n"
                f"**Direction:** {current_position.upper() if current_position else 'N/A'}\n"
                f"**Exit Reason:** Stop-loss triggered\n\n"
                f"**Trade Details:**\n"
            )
            if entry_price:
                user_msg += f"├ Entry Price: ${float(entry_price):.5f}\n"
            if exit_price:
                user_msg += f"├ Exit Price: ${exit_price:.5f}\n"
            user_msg += f"├ Lot Size: {lot_size} contracts\n"
            user_msg += f"├ SL Order ID: {order_id}\n"
            if pnl is not None:
                user_msg += f"├ {pnl_emoji} PnL: ${pnl:.4f}"
                if pnl_inr is not None:
                    user_msg += f" (₹{pnl_inr:.2f})"
                user_msg += "\n"
            user_msg += f"└ ⏰ Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"

            user_id = setup.get("user_id")
            if user_id:
                await logger_bot.send_to_user(user_id, user_msg)
        except Exception as e:
            logger.warning(f"⚠️ Failed to send SL exit notification to user: {e}")

    logger.info(f"✅ SL exit handling complete for {setup_name}")


async def reconcile_pending_orders(logger_bot=None):
    from database.crud import get_open_trade_states, get_pending_entry_trade_states, update_trade_state, get_algo_setup_by_id, get_screener_setup_by_id, get_api_credential_by_id
    from api.delta_client import DeltaExchangeClient
    from api.orders import get_order_status_by_id
    
    open_trades = await get_open_trade_states()
    pending_trades = await get_pending_entry_trade_states()
    
    for trade in open_trades + pending_trades:
        if trade.get("is_paper_trade"):
            continue
            
        trade_id = str(trade["_id"])
        setup_id = trade["setup_id"]
        setup = await get_algo_setup_by_id(setup_id) or await get_screener_setup_by_id(setup_id)
        if not setup: continue
        
        api_id = setup.get("api_id")
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred: continue
        
        client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
        symbol = trade["asset"]
        product_id = trade.get("product_id")
        
        try:
            if trade["status"] == "open":
                sl_order_id = trade.get("stop_loss_order_id")
                if sl_order_id and product_id:
                    status = await get_order_status_by_id(client, sl_order_id, product_id)
                    if status == "filled":
                        from strategy.position_manager import PositionManager
                        pm = PositionManager()
                        # The SL was hit!
                        await pm.execute_exit(client, trade, "Stop Loss Triggered")
            
            elif trade["status"] == "pending_entry":
                order_id = trade.get("pending_entry_order_id")
                if order_id and product_id:
                    status = await get_order_status_by_id(client, order_id, product_id)
                    if status in ("cancelled", "rejected", "closed"):
                        await update_trade_state(trade_id, {"status": "cancelled", "pending_entry_order_id": None})
                    elif status == "filled":
                        from strategy.position_manager import PositionManager
                        pm = PositionManager()
                        await pm.check_entry_order_filled(client, trade, None)
        finally:
            await client.close()
