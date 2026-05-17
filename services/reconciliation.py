import logging
import asyncio
from datetime import datetime
from database.crud import (
    get_all_active_algo_setups,
    get_api_credential_by_id,
    
    create_position_lock,
    delete_position_lock,
    get_db, 
    acquire_position_lock, 
    get_position_lock
)
from api.delta_client import DeltaExchangeClient
from api.orders import is_order_gone   # you already use this elsewhere
from api.positions import get_position_by_symbol
from api.orders import get_open_orders, place_stop_loss_order, cancel_order
from strategy.position_manager import PositionManager
from utils.timeframe import get_next_boundary_time
from services.logger_bot import LoggerBot
from strategy.paper_trader import is_paper_trade

logger = logging.getLogger(__name__)

async def startup_reconciliation(logger_bot: LoggerBot):
    from database.crud import get_open_trade_states, get_pending_entry_trade_states, update_trade_state, get_db, get_api_credential_by_id, get_algo_setup_by_id, get_screener_setup_by_id
    from api.delta_client import DeltaExchangeClient
    from api.positions import get_position_by_symbol
    from api.orders import get_order_status_by_id
    from strategy.position_manager import PositionManager
    
    position_manager = PositionManager()
    
    db = await get_db()
    await db["position_locks"].delete_many({})
    
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
                pos = await get_position_by_symbol(client, symbol)
                pos_size = pos.get("size", 0) if pos else 0
                
                # Detect direction flip: exchange has opposite position to what bot expects
                current_position = trade.get("direction") or trade.get("current_position")
                actual_direction = "long" if pos_size > 0 else "short" if pos_size < 0 else None
                position_flipped = pos_size != 0 and actual_direction != current_position
                
                if pos_size == 0 or position_flipped:
                    if position_flipped:
                        reason = f"Position direction flipped externally ({current_position} -> {actual_direction})"
                        await logger_bot.send_warning(
                            f"⚠️ Direction mismatch for {symbol}: bot expects {current_position.upper()} "
                            f"but exchange has {actual_direction.upper()} (size={pos_size}). Closing trade in DB."
                        )
                    else:
                        reason = "Closed manually on exchange"
                        await logger_bot.send_warning(f"⚠️ Marked trade closed for {symbol} (no exchange position)")
                    await update_trade_state(trade_id, {"status": "closed", "exit_signal": reason})
                else:
                    await acquire_position_lock(db, symbol, setup_id, setup["setup_name"], api_id=api_id)
            
            elif trade["status"] == "pending_entry":
                order_id = trade.get("pending_entry_order_id")
                if order_id and product_id:
                    status = await get_order_status_by_id(client, order_id, product_id)
                    if status in ("cancelled", "rejected", "closed"):
                        await update_trade_state(trade_id, {"status": "cancelled", "pending_entry_order_id": None})
                    elif status == "filled":
                        await position_manager.check_entry_order_filled(client, trade, None, logger_bot=logger_bot)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[STARTUP-RECON] Error processing trade {trade_id} ({symbol}): {e}")
        finally:
            await client.close()
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
