import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import (
    place_market_order, 
    place_stop_market_entry_order,
    place_stop_loss_order, 
    cancel_all_orders,
    get_open_orders,
    get_order_status_by_id,  # from the fixed orders.py
    is_order_gone
)
from api.positions import get_position_by_symbol
from api.market_data import get_product_by_symbol
from database.crud import (
    create_algo_activity, update_algo_activity, 
    update_algo_setup, get_open_activity_by_setup,
    acquire_position_lock, release_position_lock, 
    get_position_lock, get_db
)
from indicators.signal_generator import SignalGenerator
from config.settings import settings
from database.crud import create_order_record, update_order_record
from database.crud import create_position_record

logger = logging.getLogger(__name__)

class PositionManager:
    """Manage breakout entries, stop-loss protection, and exits with asset locking."""
    
    def __init__(self):
        self.signal_generator = SignalGenerator()
    
    async def place_breakout_entry_order(self, client: DeltaExchangeClient, 
                                        algo_setup: Dict[str, Any],
                                        entry_side: str, 
                                        breakout_price: float,
                                        sirusu_value: float,
                                        immediate: bool = False) -> bool:
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")

            current_position = algo_setup.get("current_position")
            if current_position:
                logger.error(f"❌ ENTRY REJECTED: {setup_name} already has {current_position.upper()} position")
                return False

            pending_entry_id = algo_setup.get("pending_entry_order_id")
            if pending_entry_id:
                logger.error(f"❌ ENTRY REJECTED: Pending entry order already exists")
                return False

            db = await get_db()
            lock_acquired = await acquire_position_lock(db, symbol, setup_id, setup.get("setup_name"))
            if not lock_acquired:
                logger.error(f"❌ ENTRY REJECTED: {symbol} is already traded by another setup")
                lock = await get_position_lock(db, symbol)
                logger.error(f"Reconciliation: Asset {symbol} already locked by {lock['setup_id']} ({lock.get('setup_name')})")
            else:
                logger.info(f"Reconciliation: Lock acquired for {symbol} by setup {setup_id}")
                return False

            actual_position = await get_position_by_symbol(client, symbol)
            actual_size = actual_position.get("size", 0) if actual_position else 0
            if actual_size != 0:
                logger.error(f"❌ ENTRY REJECTED: {symbol} has {actual_size} contracts on exchange!")
                await release_position_lock(db, symbol, setup_id)
                return False

            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if not product:
                    logger.error(f"❌ Product not found: {symbol}")
                    await release_position_lock(db, symbol, setup_id)
                    return False
                product_id = product["id"]
                await update_algo_setup(setup_id, {"product_id": product_id})

            order_side = "buy" if entry_side == "long" else "sell"
            await cancel_all_orders(client, product_id)

            if immediate:
                entry_order = await place_market_order(
                    client, product_id, lot_size, order_side
                )
                if not entry_order:
                    logger.error(f"❌ Failed to place market entry order")
                    await release_position_lock(db, symbol, setup_id)
                    return False

                # The following block is new:
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

                entry_order_id = entry_order.get("id")
                entry_price = float(entry_order.get("average_fill_price", breakout_price))
                activity_data = {
                    "user_id": algo_setup["user_id"],
                    "algo_setup_id": setup_id,
                    "algo_setup_name": setup_name,
                    "entry_time": datetime.utcnow(),
                    "entry_price": entry_price,
                    "direction": entry_side,
                    "lot_size": lot_size,
                    "asset": symbol,
                    "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend",
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "is_closed": False
                }
                await create_algo_activity(activity_data)
                await update_algo_setup(setup_id, {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "last_signal_time": datetime.utcnow(),
                    "position_lock_acquired": True
                })
                logger.info(f"🔍 About to create position record for {symbol}")
                await create_position_record({
                    "algo_setup_id": setup_id,
                    "user_id": algo_setup.get("user_id"),
                    "product_id": product_id,  # <-- ADD THIS
                    "asset": symbol,
                    "direction": entry_side,
                    "side": "buy" if entry_side == "long" else "sell",
                    "size": lot_size,
                    "entry_price": entry_price,
                    "opened_at": datetime.utcnow(),
                    "status": "open",
                    "source": "algo"
                })
                logger.info(f"✅ Position record created successfully")
                
                if algo_setup.get("additional_protection", False):
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sirusu_value, setup_id
                    )
                    logger.info(f"✅ Stop-loss placed with ID: {sl_order_id}")
                return True

            entry_order = await place_stop_market_entry_order(
                client, product_id, lot_size, order_side, breakout_price
            )
            if not entry_order:
                logger.error(f"❌ Failed to place breakout entry order")
                await release_position_lock(db, symbol, setup_id)
                return False

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

            entry_order_id = entry_order.get("id")
            await update_algo_setup(setup_id, {
                "pending_entry_order_id": entry_order_id,
                "entry_trigger_price": breakout_price,
                "pending_entry_direction_signal": 1 if entry_side == "long" else -1,
                "last_signal_time": datetime.utcnow(),
                "position_lock_acquired": True
            })
            return True
        except Exception as e:
            logger.error(f"❌ Exception placing breakout entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                db = await get_db()
                await release_position_lock(db, symbol, setup_id)
                logger.warning(f"⚠️ Lock released due to exception")
            except:
                pass
            return False

    async def check_entry_order_filled(self, client: DeltaExchangeClient,
                                      algo_setup: Dict[str, Any],
                                      sirusu_value: float) -> bool:
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            pending_order_id = algo_setup.get("pending_entry_order_id")
            
            if not pending_order_id or not product_id:
                return False

            filled = await is_order_gone(client, pending_order_id, product_id)
            if filled:
                logger.info(f"✅ Stop-market entry filled for {setup_name}")
                # ✅ ADD THIS: Update order record to "filled"
                await update_order_record(pending_order_id, {
                    "status": "filled",
                    "filled_at": datetime.utcnow()
                })
            
                # Get entry details
                entry_side = "long" if algo_setup.get("pending_entry_direction_signal") == 1 else "short"
                entry_price = algo_setup.get("entry_trigger_price")
            
                # Create position record
                logger.info(f"🔍 Creating position record for stop-market fill: {symbol}")
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
                logger.info(f"✅ Position record created")
            
                # Create activity
                activity_data = {
                    "user_id": algo_setup["user_id"],
                    "algo_setup_id": setup_id,
                    "algo_setup_name": setup_name,
                    "entry_time": datetime.utcnow(),
                    "entry_price": entry_price,
                    "direction": entry_side,
                    "lot_size": lot_size,
                    "asset": symbol,
                    "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend",
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "is_closed": False
                }
                await create_algo_activity(activity_data)
            
                # Update setup
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "last_signal_time": datetime.utcnow()
                })
            
                # Place stop-loss if protection enabled
                if algo_setup.get("additional_protection", False):
                    await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sirusu_value, setup_id
                    )
        
            return filled
                
        except Exception as e:
            logger.error(f"❌ Exception checking entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _place_stop_loss_protection(self, client: DeltaExchangeClient, 
                                         product_id: int, lot_size: int, 
                                         position_side: str, stop_price: float,
                                         setup_id: Optional[str] = None) -> Optional[int]:
        try:
            sl_side = "sell" if position_side == "long" else "buy"
            sl_order = await place_stop_loss_order(
                client, product_id, lot_size, sl_side, stop_price, use_stop_market=True
            )
            if sl_order:
                sl_order_id = sl_order.get("id")
                logger.info(f"✅ Stop-loss order placed successfully (ID: {sl_order_id})")
                if setup_id:
                    await update_algo_setup(setup_id, {
                        "stop_loss_order_id": sl_order_id
                    })
                    logger.info(f"💾 Saved stop-loss order ID {sl_order_id} to database")

                order_data = {
                    "order_id": sl_order_id,
                    "algo_setup_id": setup_id,
                    "user_id": None,  # Pass user_id if available
                    "asset": None,    # If symbol available, put here
                    "side": sl_side,
                    "size": lot_size,
                    "order_type": sl_order.get("order_type"),
                    "status": sl_order.get("state", "submitted"),
                    "limit_price": sl_order.get("limit_price"),
                    "stop_price": sl_order.get("stop_price"),
                    "reduce_only": sl_order.get("reduce_only"),
                    "average_fill_price": sl_order.get("average_fill_price"),
                    "extra_data": sl_order,
                }
                await create_order_record(order_data)
                return sl_order_id
            else:
                logger.warning(f"⚠️ Failed to place stop-loss order")
                return None
        except Exception as e:
            logger.error(f"❌ Exception placing stop-loss: {e}")
            return None

    async def execute_exit(self, client: DeltaExchangeClient, 
                          algo_setup: Dict[str, Any],
                          sirusu_signal_text: str) -> bool:
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            current_position = algo_setup.get("current_position")
            stop_loss_order_id = algo_setup.get("stop_loss_order_id")

            if not current_position or not product_id:
                logger.warning(f"⚠️ No current position or product_id for {symbol}")
                return False

            # Check if stop-loss is already filled/cancelled
            sl_executed = False
            if stop_loss_order_id:
                sl_executed = await is_order_gone(client, stop_loss_order_id, product_id)

            if sl_executed:
                logger.warning(f"⚠️ Position for {symbol} already closed by stop-loss!")
                
                # ✅ ADD THIS: Update SL order record
                await update_order_record(stop_loss_order_id, {
                    "status": "filled",
                    "filled_at": datetime.utcnow()
                })

                activity = await get_open_activity_by_setup(setup_id)
                if activity:
                    await update_algo_activity(str(activity["_id"]), {
                        "exit_time": datetime.utcnow(),
                        "exit_price": None,
                        "sirusu_exit_signal": f"Stop-loss triggered ({sirusu_signal_text})",
                        "is_closed": True
                    })
                await update_algo_setup(setup_id, {
                    "current_position": None,
                    "last_entry_price": None,
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "stop_loss_order_id": None,
                    "position_lock_acquired": False
                })
            
                db = await get_db()
                await db.positions.update_many(
                    {"algo_setup_id": setup_id, "status": "open"},
                    {"$set": {"closed_at": datetime.utcnow(), "status": "closed"}}
                )
                await release_position_lock(db, symbol, setup_id)
                return True  # <-- ADD THIS!

            # Place market exit
            exit_side = "sell" if current_position == "long" else "buy"
            exit_order = await place_market_order(client, product_id, lot_size, exit_side)
            exit_price = float(exit_order.get("average_fill_price", 0)) if exit_order else None
            if not exit_order:
                return False

            # Save order event
            order_data = {
                "order_id": exit_order.get("id"),
                "algo_setup_id": setup_id,
                "user_id": algo_setup.get("user_id"),
                "asset": symbol,
                "side": exit_side,
                "size": lot_size,
                "order_type": exit_order.get("order_type"),
                "status": exit_order.get("state", "submitted"),
                "limit_price": exit_order.get("limit_price"),
                "stop_price": exit_order.get("stop_price"),
                "reduce_only": exit_order.get("reduce_only"),
                "average_fill_price": exit_order.get("average_fill_price"),
                "extra_data": exit_order,
            }
            await create_order_record(order_data)

            # Cancel stop-loss after market exit
            if stop_loss_order_id:
                try:
                    from api.orders import cancel_order
                    cancelled = await cancel_order(client, stop_loss_order_id)
        
                    if cancelled:
                        # ✅ ADD THIS: Mark as cancelled
                        await update_order_record(stop_loss_order_id, {
                            "status": "cancelled",
                            "updated_at": datetime.utcnow()
                        })
                except Exception:
                    pass

            activity = await get_open_activity_by_setup(setup_id)
            if activity and exit_price:
                entry_price = activity.get("entry_price", 0)
                pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position)
                pnl_inr = pnl * settings.usd_to_inr_rate
                await update_algo_activity(str(activity["_id"]), {
                    "exit_time": datetime.utcnow(),
                    "exit_price": exit_price,
                    "pnl": round(pnl, 4),
                    "pnl_inr": round(pnl_inr, 2),
                    "sirusu_exit_signal": sirusu_signal_text,
                    "is_closed": True
                })
            await update_algo_setup(setup_id, {
                "current_position": None,
                "last_entry_price": None,
                "pending_entry_order_id": None,
                "entry_trigger_price": None,
                "stop_loss_order_id": None,
                "position_lock_acquired": False
            })
            db = await get_db()
            await release_position_lock(db, symbol, setup_id)
            return True
        except Exception as e:
            logger.error(f"❌ Exception in execute_exit: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _calculate_pnl(self, entry_price: float, exit_price: float, 
                      lot_size: int, position_side: str) -> float:
        if position_side == "long":
            pnl = (exit_price - entry_price) * lot_size
        else:
            pnl = (entry_price - exit_price) * lot_size
        return pnl

    async def sync_exchange_positions_and_orders(self, client: DeltaExchangeClient, all_setups: list):
        """
        On startup, sync open positions and pending orders from the exchange with local database.
        """
        for setup in all_setups:
            symbol = setup.get("asset")
            setup_id = str(setup["_id"])
            product_id = setup.get("product_id")

            # ✅ ADD THIS: Ensure product_id exists before fetching position
            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if product:
                    product_id = product["id"]
                    await update_algo_setup(setup_id, {"product_id": product_id})
                    logger.info(f"✅ Product ID {product_id} saved for {symbol}")
                else:
                    logger.error(f"❌ Product not found for {symbol}, skipping reconciliation")
                    continue  # Skip this setup if product doesn't exist
                
            # 1. Check for open position
            position = await get_position_by_symbol(client, symbol)
            position_size = position.get("size", 0) if position else 0

            if position_size != 0:
                # Get direction from position size
                direction = "long" if position_size > 0 else "short"
                
                await update_algo_setup(setup_id, {
                    "current_position": "long" if position_size > 0 else "short",
                    "last_entry_price": position.get("entry_price"),
                    "position_lock_acquired": True,
                    "last_signal_time": datetime.utcnow(),
                })

                # Create position record
                logger.info(f"🔍 About to create position record for {symbol} (size={position_size})")
                
                await create_position_record({
                    "algo_setup_id": setup_id,
                    "user_id": setup.get("user_id"),
                    "product_id": product_id,  # ✅ ADD THIS
                    "asset": symbol,
                    "direction": direction,
                    "side": "buy" if direction == "long" else "sell",
                    "size": abs(position_size),
                    "entry_price": position.get("entry_price"),
                    "opened_at": datetime.utcnow(),
                    "status": "open",
                    "source": "reconciliation"
                })
                logger.info(f"✅ Position record created successfully")
                
                # Optionally recreate algo_activity here if needed
                activity_data = {
                    "user_id": setup.get("user_id"),
                    "algo_setup_id": setup_id,
                    "algo_setup_name": setup.get("setup_name"),
                    "entry_time": datetime.utcnow(),
                    "entry_price": position.get("entry_price"),
                    "direction": direction,
                    "lot_size": abs(position_size),
                    "asset": symbol,
                    "perusu_entry_signal": "uptrend" if direction == "long" else "downtrend",
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "is_closed": False
                }
                await create_algo_activity(activity_data)
                logger.info(f"✅ Algo activity created for reconciled position: {symbol}")
            
                # Acquire position lock
                db = await get_db()
                await acquire_position_lock(db, symbol, setup_id, setup.get("setup_name"))

            # 2. Check for open orders (entry, stop-loss, etc.)
            open_orders = await get_open_orders(client, product_id)
            for order in (open_orders or []):
                state = order.get("state")
                if state in ("open", "untriggered"):
                    # Re-save as pending order if needed
                    order_type = order.get("order_type")
                    if order_type == "stop_market_order":
                        await update_algo_setup(setup_id, {
                            "pending_entry_order_id": order.get("id"),
                            "entry_trigger_price": order.get("stop_price"),
                            "pending_entry_direction_signal": 1 if order.get("side")=="buy" else -1,
                            "last_signal_time": datetime.utcnow(),
                        })
                    elif order_type == "market_order" and order.get("reduce_only"):
                        await update_algo_setup(setup_id, {
                            "stop_loss_order_id": order.get("id"),
                        })
                        
