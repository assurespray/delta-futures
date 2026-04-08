import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import (
    place_market_order, 
    place_stop_market_entry_order,
    place_stop_loss_order, 
    cancel_all_orders,
    cancel_order,
    get_open_orders,
    get_order_history,
    get_order_status_by_id,  # from the fixed orders.py
    is_order_gone,
    format_orders_display       # <-- Is used in orders_callback
)
from api.positions import get_position_by_symbol
from api.market_data import get_product_by_symbol
from database.crud import (
    create_trade_state, update_trade_state, 
    get_open_trade_by_setup, update_algo_setup, update_screener_setup,
    acquire_position_lock, release_position_lock, 
    get_position_lock, get_db
)
from indicators.signal_generator import SignalGenerator
from config.settings import settings
from database.crud import create_order_record, update_order_record
from database.crud import create_position_record
from strategy.paper_trader import is_paper_trade, paper_trader

logger = logging.getLogger(__name__)

class PositionManager:
    """Manage breakout entries, stop-loss protection, and exits with asset locking."""
    
    def __init__(self):
        from indicators.signal_generator import SignalGenerator
        self.signal_generator = SignalGenerator()

    async def place_breakout_entry_order(self, client: DeltaExchangeClient, 
                                        algo_setup: Dict[str, Any],
                                        entry_side: str, 
                                        breakout_price: float,
                                        sirusu_value: float,
                                        immediate: bool = False) -> bool:
        try:
            # ========== PAPER TRADE ROUTING ==========
            if is_paper_trade(algo_setup):
                return await paper_trader.place_virtual_entry(
                    client=client,
                    algo_setup=algo_setup,
                    entry_side=entry_side,
                    breakout_price=breakout_price,
                    sirusu_value=sirusu_value,
                    immediate=immediate
                )
            # ========== END PAPER TRADE ROUTING ==========
            
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            setup_type = "screener" if "asset_selection_type" in algo_setup else "algo"

            db = await get_db()
            lock = await get_position_lock(db, symbol)
            if lock and lock['setup_id'] != setup_id:
                logger.error(f"❌ ENTRY REJECTED: {symbol} is already traded by setup {lock['setup_id']} ({lock.get('setup_name')})")
                return False

            lock_acquired = await acquire_position_lock(db, symbol, setup_id, setup_name)
            if not lock_acquired:
                logger.error(f"❌ Failed to acquire lock for {symbol} by {setup_name}")
                return False

            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if product:
                    product_id = product["id"]
                    
            order_side = "buy" if entry_side == "long" else "sell"
            
            trade_data = {
                "user_id": algo_setup.get("user_id"),
                "setup_id": setup_id,
                "setup_type": setup_type,
                "setup_name": setup_name,
                "asset": symbol,
                "product_id": product_id,
                "direction": entry_side,
                "lot_size": lot_size,
                "timeframe": algo_setup.get("timeframe", "1m"),
                "status": "pending_entry",
                "entry_trigger_price": breakout_price,
                "pending_entry_direction_signal": 1 if entry_side == "long" else -1,
                "pending_entry_side": entry_side,
                "pending_sl_price": sirusu_value,
                "is_paper_trade": False,
                "last_signal_time": datetime.utcnow()
            }

            if immediate:
                entry_order = await place_market_order(client, product_id, lot_size, order_side)
                if not entry_order:
                    logger.error(f"❌ Failed to place market entry order")
                    await release_position_lock(db, symbol, setup_id)
                    return False

                entry_order_id = entry_order.get("id")
                raw_fill_price = entry_order.get("average_fill_price")
                entry_price = float(raw_fill_price) if raw_fill_price is not None else float(breakout_price)
                
                trade_data["status"] = "open"
                trade_data["entry_price"] = entry_price
                trade_data["last_entry_order_id"] = entry_order_id
                trade_data["current_position"] = entry_side
                trade_data["entry_time"] = datetime.utcnow()
                trade_data["perusu_entry_signal"] = "uptrend" if entry_side == "long" else "downtrend"
                trade_data["trade_date"] = datetime.utcnow().strftime("%Y-%m-%d")
                
                from database.crud import create_trade_state, update_trade_state, create_position_record, create_order_record
                
                trade_id = await create_trade_state(trade_data)
                
                await create_position_record({
                    "algo_setup_id": setup_id,
                    "user_id": algo_setup.get("user_id"),
                    "product_id": product_id,
                    "asset": symbol,
                    "direction": entry_side,
                    "side": "buy" if entry_side == "long" else "sell",
                    "size": lot_size,
                    "entry_price": entry_price,
                    "opened_at": datetime.utcnow(),
                    "status": "open",
                    "source": "algo"
                })
                
                if algo_setup.get("additional_protection", False):
                    sl_price = sirusu_value
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sl_price,
                        setup_id, symbol, algo_setup.get("user_id")
                    )
                    await update_trade_state(trade_id, {"stop_loss_order_id": sl_order_id})
                
                return True

            entry_order = await place_stop_market_entry_order(
                client, product_id, lot_size, order_side, breakout_price
            )
            if not entry_order:
                logger.error(f"❌ Failed to place breakout entry order")
                await release_position_lock(db, symbol, setup_id)
                return False
                
            from database.crud import create_order_record, create_trade_state
            order_data = {
                "order_id": entry_order.get("id"),
                "algo_setup_id": setup_id,
                "user_id": algo_setup.get("user_id"),
                "asset": symbol,
                "side": order_side,
                "size": lot_size,
                "order_type": entry_order.get("order_type"),
                "status": entry_order.get("state", "submitted"),
                "limit_price": entry_order.get("limit_price"),
                "stop_price": entry_order.get("stop_price"),
                "reduce_only": entry_order.get("reduce_only"),
                "average_fill_price": entry_order.get("average_fill_price"),
                "extra_data": entry_order,
            }
            await create_order_record(order_data)
                
            trade_data["pending_entry_order_id"] = entry_order.get("id")
            await create_trade_state(trade_data)
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception placing breakout entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def check_entry_order_filled(self, client: DeltaExchangeClient,
                                      trade_state: Dict[str, Any],
                                      sirusu_value: float = None) -> bool:
        try:
            if trade_state.get("is_paper_trade", False):
                return False
            
            trade_id = str(trade_state["_id"])
            setup_id = trade_state["setup_id"]
            setup_name = trade_state["setup_name"]
            symbol = trade_state["asset"]
            lot_size = trade_state["lot_size"]
            product_id = trade_state.get("product_id")
            pending_order_id = trade_state.get("pending_entry_order_id")
            
            if not pending_order_id or not product_id:
                return False

            order_status = await get_order_status_by_id(client, pending_order_id, product_id)
            logger.info(f"[FILL-MONITOR] Order {pending_order_id} status: {order_status}")

            if order_status in ("filled", "closed", "triggered"):
                from database.crud import update_trade_state, create_position_record
                success = await update_trade_state(trade_id, {"pending_entry_order_id": None})
                if not success: return False
                    
                entry_side = trade_state.get("pending_entry_side", "long")
                position = await get_position_by_symbol(client, symbol)
                entry_price = float(position["entry_price"]) if position else float(trade_state.get("entry_trigger_price", 0))

                update_data = {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "last_entry_order_id": pending_order_id,
                    "status": "open",
                    "entry_time": datetime.utcnow(),
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend"
                }
                
                await update_trade_state(trade_id, update_data)
                
                await create_position_record({
                    "algo_setup_id": setup_id,
                    "user_id": trade_state.get("user_id"),
                    "product_id": product_id,
                    "asset": symbol,
                    "direction": entry_side,
                    "side": "buy" if entry_side == "long" else "sell",
                    "size": lot_size,
                    "entry_price": entry_price,
                    "opened_at": datetime.utcnow(),
                    "status": "open",
                    "source": "algo"
                })

                sl_price = sirusu_value
                if not sl_price:
                    # Fetch absolute latest Sirusu value from IndicatorCache
                    from database.mongodb import mongodb
                    db = mongodb.get_db()
                    cache = await db.indicator_cache.find_one({
                        "setup_id": setup_id,
                        "asset": symbol,
                        "timeframe": trade_state.get("timeframe")
                    })
                    if cache and cache.get("sirusu_value"):
                        sl_price = cache.get("sirusu_value")
                        logger.info(f"✅ Fetched latest Sirusu value from cache: ${sl_price}")
                    else:
                        sl_price = trade_state.get("pending_sl_price")

                if trade_state.get("additional_protection", False) and sl_price:
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sl_price,
                        setup_id, symbol, trade_state.get("user_id")
                    )
                    await update_trade_state(trade_id, {"stop_loss_order_id": sl_order_id})
                    
                logger.info(f"✅ [FILL-MONITOR] Processed fill for {symbol} ({setup_name})")
                return True

            elif order_status in ("cancelled", "rejected"):
                logger.warning(f"⚠️ [FILL-MONITOR] Order {pending_order_id} was {order_status}. Cleaning up.")
                from database.crud import update_trade_state
                await update_trade_state(trade_id, {"status": "cancelled", "pending_entry_order_id": None})
                db = await get_db()
                await release_position_lock(db, symbol, setup_id)
                return False

            return False
            
        except Exception as e:
            logger.error(f"❌ Exception checking entry fill: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _place_stop_loss_protection(self, client: DeltaExchangeClient, product_id: int, 
                                          lot_size: int, entry_side: str, sl_price: float,
                                          setup_id: str, symbol: str, user_id: str,
                                          existing_order_id: Optional[str] = None) -> Optional[int]:
        try:
            from api.orders import cancel_order
            if existing_order_id:
                try:
                    await cancel_order(client, product_id, existing_order_id)
                except Exception as e:
                    logger.warning(f"Could not cancel old SL {existing_order_id}: {e}")

            sl_side = "sell" if entry_side == "long" else "buy"
            sl_order = await place_stop_loss_order(
                client, product_id, lot_size, sl_side, sl_price
            )
            
            if sl_order:
                from database.crud import create_order_record
                order_data = {
                    "order_id": sl_order.get("id"),
                    "algo_setup_id": setup_id,
                    "user_id": user_id,
                    "asset": symbol,
                    "side": sl_side,
                    "size": lot_size,
                    "order_type": sl_order.get("order_type"),
                    "status": sl_order.get("state", "submitted"),
                    "stop_price": sl_order.get("stop_price"),
                    "reduce_only": True,
                    "extra_data": sl_order,
                }
                await create_order_record(order_data)
                return sl_order.get("id")
            return None
        except Exception as e:
            logger.error(f"❌ Exception placing SL protection: {e}")
            return None

    async def execute_exit(self, client: DeltaExchangeClient,
                           trade_state: dict,
                           sirusu_signal_text: str) -> tuple[bool, float, str]:
        try:
            if trade_state.get("is_paper_trade", False):
                return await paper_trader.execute_virtual_exit(
                    client=client, trade_state=trade_state, exit_reason=f"Sirusu flip to {sirusu_signal_text}"
                )
            
            trade_id = str(trade_state["_id"])
            setup_id = trade_state["setup_id"]
            setup_name = trade_state["setup_name"]
            symbol = trade_state["asset"]
            lot_size = trade_state["lot_size"]
            product_id = trade_state.get("product_id")
            current_position = trade_state.get("current_position")
            stop_loss_order_id = trade_state.get("stop_loss_order_id")

            if not current_position or not product_id:
                logger.warning(f"⚠️ No current position or product_id for {symbol}")
                return False, 0.0, ""

            logger.info(f"🚪 Executing exit for {setup_name} - {current_position.upper()} position")
            
            actual_position = await get_position_by_symbol(client, symbol)
            actual_size = actual_position.get("size", 0) if actual_position else 0
            exit_size = abs(actual_size) if actual_size != 0 else lot_size
            
            if stop_loss_order_id:
                await self._cancel_stop_loss_orders(client, product_id, symbol, stop_loss_order_id)

            exit_side = "sell" if current_position == "long" else "buy"
            exit_order = await place_market_order(client, product_id, exit_size, exit_side, reduce_only=True)
            
            if not exit_order:
                logger.error(f"❌ Failed to place exit order for {symbol}")
                return False, 0.0, ""

            raw_fill_price = exit_order.get("average_fill_price")
            exit_price = float(raw_fill_price) if raw_fill_price is not None else 0.0
            
            entry_price = trade_state.get("entry_price", 0)
            pnl = 0.0
            if entry_price and exit_price:
                if current_position == "long":
                    pnl = (exit_price - entry_price) * exit_size
                else:
                    pnl = (entry_price - exit_price) * exit_size
                    
            pnl_inr = pnl * settings.usd_to_inr_rate
            
            from database.crud import update_trade_state
            await update_trade_state(trade_id, {
                "status": "closed",
                "exit_price": exit_price,
                "exit_time": datetime.utcnow(),
                "pnl": pnl,
                "pnl_inr": pnl_inr,
                "sirusu_exit_signal": sirusu_signal_text
            })

            db = await get_db()
            await release_position_lock(db, symbol, setup_id)
            
            return True, exit_price, sirusu_signal_text

        except Exception as e:
            logger.error(f"❌ Exception executing exit: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, 0.0, ""

    async def _cancel_stop_loss_orders(self, client: DeltaExchangeClient, product_id: int,
                                     symbol: str, stop_loss_order_id: Optional[str] = None) -> None:
        try:
            from services.reconciliation import filter_orders_by_symbol_and_product_id
            from api.orders import cancel_order
            
            open_orders = await get_open_orders(client)
            if open_orders:
                orders_for_symbol = filter_orders_by_symbol_and_product_id(open_orders, symbol, product_id)
                stop_orders = [o for o in orders_for_symbol if o.get("order_type") == "stop_order"]
                
                if stop_loss_order_id:
                    for order in stop_orders:
                        if str(order.get("id")) == str(stop_loss_order_id):
                            await cancel_order(client, product_id, order["id"])
                            logger.info(f"✅ Cancelled specific SL order {stop_loss_order_id} for {symbol}")
                            return
                
                for order in stop_orders:
                    await cancel_order(client, product_id, order["id"])
                    logger.info(f"✅ Cancelled generic SL order {order['id']} for {symbol}")
        except Exception as e:
            logger.error(f"❌ Error cancelling SL orders for {symbol}: {e}")

