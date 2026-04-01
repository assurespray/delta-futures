"""Pending order monitoring and management."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import (
    get_order_by_id, cancel_order, place_stop_loss_order,
    is_order_gone, get_order_status_by_id, get_open_orders,
    cancel_all_orders
)
from database.crud import (
    update_algo_setup, create_algo_activity, get_indicator_cache,
    update_order_record, release_position_lock, get_db
)

logger = logging.getLogger(__name__)


class OrderMonitor:
    """Monitor and manage pending entry orders."""
    
    def __init__(self):
        """Initialize order monitor."""
        pass

    async def check_pending_entry_order(
        self,
        client,
        algo_setup: Dict[str, Any],
        current_perusu_signal: int,
        current_sirusu_signal: int,
        sirusu_value: float,
        logger_bot
    ) -> Optional[str]:
        """
        Check pending entry order status.
        FIXED: Uses exact order status (not just is_order_gone).
        Cancels BOTH entry AND stop-loss on SIRUSU reversal.
        Releases position lock on invalidation.

        Returns: "filled", "cancelled", "reversed", "pending", or None
        """
        pending_order_id = algo_setup.get('pending_entry_order_id')
        if not pending_order_id:
            return None

        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        symbol = algo_setup.get('asset')
        pending_side = algo_setup.get('pending_entry_side') or \
            ("long" if algo_setup.get('pending_entry_direction_signal') == 1 else "short")
        product_id = algo_setup.get('product_id')
        stop_loss_order_id = algo_setup.get('stop_loss_order_id')

        logger.info(f"📋 Checking pending {pending_side} entry order: {pending_order_id}")

        try:
            # FIXED: Get EXACT order status instead of just is_order_gone
            order_status = await get_order_status_by_id(client, pending_order_id, product_id)
            logger.info(f"   Order {pending_order_id} exact status: {order_status}")

            if order_status in ("filled", "closed", "triggered"):
                logger.info(f"✅ Pending {pending_side} entry order FILLED (status={order_status})")
                # Don't clear state here - let check_entry_order_filled handle the fill logic
                return "filled"

            if order_status in ("cancelled", "rejected"):
                # Order was already cancelled (by us or exchange) - clean up fully
                logger.warning(f"⚠️ Pending entry order {pending_order_id} is {order_status}")
                await self._invalidate_setup(
                    client, setup_id, setup_name, symbol, product_id,
                    pending_order_id, stop_loss_order_id,
                    f"Entry order {order_status}", logger_bot
                )
                return "cancelled"

            if order_status == "not_found":
                # Order disappeared - check exchange position to decide
                logger.warning(f"⚠️ Order {pending_order_id} not found anywhere")
                # Leave it to the fill monitor to resolve via position check
                return "pending"

            # Order is still live (open/untriggered) - check for Sirusu reversal
            cached_sirusu = await get_indicator_cache(setup_id, "sirusu")
            last_sirusu_signal = cached_sirusu.get('last_signal') if cached_sirusu else None

            if last_sirusu_signal is None:
                logger.warning(f"⚠️ No cached Sirusu signal - cannot check reversal")
                return "pending"

            sirusu_flipped = False
            if pending_side == "long":
                if current_sirusu_signal == -1 and last_sirusu_signal == 1:
                    sirusu_flipped = True
                    logger.warning(f"🔄 SIRUSU REVERSAL: Uptrend -> Downtrend")
            elif pending_side == "short":
                if current_sirusu_signal == 1 and last_sirusu_signal == -1:
                    sirusu_flipped = True
                    logger.warning(f"🔄 SIRUSU REVERSAL: Downtrend -> Uptrend")

            if sirusu_flipped:
                logger.warning(f"❌ Sirusu flipped - invalidating {pending_side} setup for {setup_name}")
                await self._invalidate_setup(
                    client, setup_id, setup_name, symbol, product_id,
                    pending_order_id, stop_loss_order_id,
                    "Sirusu signal reversed - trade no longer valid", logger_bot
                )
                return "reversed"
            else:
                logger.info(f"⏳ Order still pending - Sirusu has not reversed")
                logger.info(f"   Last Sirusu: {last_sirusu_signal}, Current: {current_sirusu_signal}")
                return "pending"

        except Exception as e:
            logger.error(f"❌ Error checking pending order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _invalidate_setup(
        self,
        client,
        setup_id: str,
        setup_name: str,
        symbol: str,
        product_id: int,
        pending_order_id,
        stop_loss_order_id,
        reason: str,
        logger_bot
    ):
        """
        Fully invalidate a pending trade setup:
        1. Cancel the pending entry order on exchange
        2. Cancel any stop-loss order on exchange
        3. Clear all pending state from database
        4. Release position lock
        5. Notify via Telegram
        """
        cancelled_entry = False
        cancelled_sl = False

        # 1. Cancel the pending entry order
        if pending_order_id:
            logger.info(f"🗑️ Cancelling pending entry order {pending_order_id}...")
            try:
                cancelled_entry = await cancel_order(client, product_id, pending_order_id)
                if cancelled_entry:
                    logger.info(f"✅ Entry order {pending_order_id} cancelled")
                    await update_order_record(pending_order_id, {
                        "status": "cancelled",
                        "updated_at": datetime.utcnow()
                    })
                else:
                    logger.warning(f"⚠️ Failed to cancel entry order {pending_order_id}")
            except Exception as e:
                logger.error(f"❌ Error cancelling entry order: {e}")

        # 2. Cancel the stop-loss order (placed at the same time as entry)
        if stop_loss_order_id:
            logger.info(f"🗑️ Cancelling stop-loss order {stop_loss_order_id}...")
            try:
                cancelled_sl = await cancel_order(client, product_id, stop_loss_order_id)
                if cancelled_sl:
                    logger.info(f"✅ Stop-loss order {stop_loss_order_id} cancelled")
                    await update_order_record(stop_loss_order_id, {
                        "status": "cancelled",
                        "updated_at": datetime.utcnow()
                    })
                else:
                    logger.warning(f"⚠️ Failed to cancel SL order {stop_loss_order_id}")
            except Exception as e:
                logger.error(f"❌ Error cancelling SL order: {e}")

        # 2b. Safety sweep: cancel ALL remaining orders for this product
        #     (catches any orphaned orders not tracked in DB)
        if product_id:
            try:
                open_orders = await get_open_orders(client, product_id)
                if open_orders:
                    for order in open_orders:
                        oid = order.get("id")
                        if oid and oid != pending_order_id and oid != stop_loss_order_id:
                            logger.info(f"🧹 Cancelling orphaned order {oid} for product {product_id}")
                            await cancel_order(client, product_id, oid)
            except Exception as e:
                logger.warning(f"⚠️ Error during safety sweep: {e}")

        # 3. Clear ALL pending state from database
        await update_algo_setup(setup_id, {
            "pending_entry_order_id": None,
            "pending_entry_side": None,
            "pending_entry_direction_signal": None,
            "entry_trigger_price": None,
            "pending_sl_price": None,
            "stop_loss_order_id": None,
            "position_lock_acquired": False
        })

        # 4. Release position lock
        try:
            db = await get_db()
            await release_position_lock(db, symbol, setup_id)
            logger.info(f"✅ Position lock released for {symbol}")
        except Exception as e:
            logger.error(f"❌ Error releasing position lock: {e}")

        # 5. Send Telegram notification
        if logger_bot:
            try:
                await logger_bot.send_info(
                    f"⚠️ {setup_name}: Trade setup INVALIDATED\n"
                    f"Asset: {symbol}\n"
                    f"Entry order: {'cancelled' if cancelled_entry else 'failed to cancel'}\n"
                    f"Stop-loss: {'cancelled' if cancelled_sl else ('N/A' if not stop_loss_order_id else 'failed to cancel')}\n"
                    f"Reason: {reason}"
                )
            except Exception as e:
                logger.error(f"❌ Error sending invalidation notification: {e}")

        logger.info(
            f"🧹 Setup {setup_name} fully invalidated: "
            f"entry={'cancelled' if cancelled_entry else 'failed'}, "
            f"sl={'cancelled' if cancelled_sl else 'N/A'}, "
            f"reason={reason}"
        )

    # Other helper methods would similarly use only /orders?state and /orders/history, not /orders/{order_id}
    
    async def _handle_filled_order(
        self,
        client: DeltaExchangeClient,
        algo_setup: Dict[str, Any],
        order: Dict[str, Any],
        sirusu_value: float,
        logger_bot: Optional[Any] = None
    ):
        """
        Handle a filled entry order.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            order: Order response from Delta Exchange
            sirusu_value: Current Sirusu value (for stop-loss)
            logger_bot: Logger bot for notifications
        """
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        
        logger.info(f"✅ [MONITOR] Pending order FILLED: {order.get('id')}")
        
        # Get fill details
        raw_fill_price = order.get("average_fill_price")
        entry_price = float(raw_fill_price) if raw_fill_price is not None else 0.0
        if entry_price == 0:
            raw_stop = order.get("stop_price")
            entry_price = float(raw_stop) if raw_stop is not None else 0.0
        
        entry_side = "long" if order.get("side") == "buy" else "short"
        lot_size = algo_setup['lot_size']
        
        logger.info(f"   Entry: {entry_side.upper()} {lot_size} @ ${entry_price:.5f}")
        
        # Place stop-loss if enabled
        stop_loss_order_id = None
        if algo_setup.get("additional_protection", False):
            stop_loss_order_id = await self._place_stop_loss(
                client, algo_setup, entry_side, sirusu_value
            )
        
        # Create activity record
        activity_data = {
            "user_id": algo_setup["user_id"],
            "algo_setup_id": setup_id,
            "algo_setup_name": setup_name,
            "entry_time": datetime.utcnow(),
            "entry_price": entry_price,
            "entry_trigger_price": algo_setup.get("entry_trigger_price"),
            "direction": entry_side,
            "lot_size": lot_size,
            "asset": algo_setup["asset"],
            "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend",
            "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "is_closed": False
        }
        
        activity_id = await create_algo_activity(activity_data)
        
        # Update algo setup - position is now open
        await update_algo_setup(setup_id, {
            "current_position": entry_side,
            "last_entry_price": entry_price,
            "pending_entry_order_id": None,
            "pending_entry_side": None,
            "entry_trigger_price": None,
            "pending_entry_direction_signal": None,
            "pending_sl_price": None,
            "stop_loss_order_id": stop_loss_order_id,
            "last_signal_time": datetime.utcnow()
        })
        
        logger.info(f"✅ [MONITOR] Position opened: {entry_side.upper()} (Activity ID: {activity_id})")
        
        # Send Telegram notification
        if logger_bot:
            await logger_bot.send_trade_entry(
                setup_name=setup_name,
                asset=algo_setup["asset"],
                direction=entry_side,
                entry_price=entry_price,
                lot_size=lot_size,
                perusu_signal="Uptrend" if entry_side == "long" else "Downtrend",
                sirusu_sl=sirusu_value
            )
    
    async def _handle_signal_reversal(
        self,
        client: DeltaExchangeClient,
        algo_setup: Dict[str, Any],
        pending_order_id: int,
        old_signal: int,
        new_signal: int,
        logger_bot: Optional[Any] = None
    ):
        """
        Handle Perusu signal reversal - cancel pending order.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            pending_order_id: Order ID to cancel
            old_signal: Original Perusu signal
            new_signal: Current Perusu signal
            logger_bot: Logger bot for notifications
        """
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        
        logger.warning(f"⚠️ [MONITOR] Perusu signal REVERSED! Canceling pending order")
        logger.warning(f"   Was: {old_signal} ({'Uptrend' if old_signal == 1 else 'Downtrend'})")
        logger.warning(f"   Now: {new_signal} ({'Uptrend' if new_signal == 1 else 'Downtrend'})")
        
        # Cancel the order
        product_id = algo_setup.get('product_id')
        cancelled = await cancel_order(client, product_id, pending_order_id)
        
        if cancelled:
            logger.info(f"✅ [MONITOR] Pending order cancelled successfully")
            
            # Also cancel any stop-loss order that may have been placed
            stop_loss_order_id = algo_setup.get('stop_loss_order_id')
            if stop_loss_order_id:
                logger.info(f"🗑️ [MONITOR] Cancelling associated SL order {stop_loss_order_id}")
                try:
                    await cancel_order(client, product_id, stop_loss_order_id)
                except Exception as e:
                    logger.warning(f"⚠️ Could not cancel SL order {stop_loss_order_id}: {e}")
            
            # Clear ALL pending state from database (including SL and lock)
            await update_algo_setup(setup_id, {
                "pending_entry_order_id": None,
                "pending_entry_side": None,
                "entry_trigger_price": None,
                "pending_entry_direction_signal": None,
                "pending_sl_price": None,
                "stop_loss_order_id": None,
                "position_lock_acquired": False
            })
            
            # Release position lock
            symbol = algo_setup.get('asset')
            try:
                db = await get_db()
                await release_position_lock(db, symbol, setup_id)
                logger.info(f"✅ [MONITOR] Position lock released for {symbol}")
            except Exception as e:
                logger.error(f"❌ [MONITOR] Error releasing position lock: {e}")
            
            # Send Telegram notification
            if logger_bot:
                old_signal_text = "Uptrend" if old_signal == 1 else "Downtrend"
                new_signal_text = "Uptrend" if new_signal == 1 else "Downtrend"
                await logger_bot.send_order_cancelled(
                    setup_name, old_signal_text, new_signal_text
                )
        else:
            logger.error(f"❌ [MONITOR] Failed to cancel pending order — checking if order filled")
            # Cancel failed — order may have already filled on exchange
            from api.orders import get_order_status_by_id
            try:
                actual_status = await get_order_status_by_id(client, pending_order_id, product_id)
                if actual_status in ("filled", "closed", "triggered"):
                    logger.warning(f"⚠️ [MONITOR] Order {pending_order_id} already filled (status={actual_status}) — leaving for fill monitor")
                else:
                    logger.error(f"❌ [MONITOR] Order {pending_order_id} status={actual_status} — cancel failed and order not filled")
            except Exception as e:
                logger.error(f"❌ [MONITOR] Could not check order status after cancel failure: {e}")
    
    async def _place_stop_loss(
        self,
        client: DeltaExchangeClient,
        algo_setup: Dict[str, Any],
        position_side: str,
        stop_price: float
    ):
        """
        Place stop-loss order for position protection.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            position_side: "long" or "short"
            stop_price: Stop-loss trigger price (Sirusu value)
        """
        product_id = algo_setup.get("product_id")
        lot_size = algo_setup["lot_size"]
        
        # Determine stop-loss order side (opposite of position)
        sl_side = "sell" if position_side == "long" else "buy"
        
        logger.info(f"🛡️ [MONITOR] Placing stop-loss: {sl_side.upper()} @ ${stop_price:.5f}")
        
        sl_order = await place_stop_loss_order(
            client, product_id, lot_size, sl_side, stop_price, use_stop_market=True
        )
        
        if sl_order:
            sl_order_id = sl_order.get('id')
            logger.info(f"✅ [MONITOR] Stop-loss placed (ID: {sl_order_id})")
            return sl_order_id
        else:
            logger.warning(f"⚠️ [MONITOR] Failed to place stop-loss")
            return None
    
    async def _clean_pending_order(self, setup_id: str):
        """
        Clean up pending order data from database.
        
        Args:
            setup_id: Algo setup ID
        """
        await update_algo_setup(setup_id, {
            "pending_entry_order_id": None,
            "pending_entry_side": None,
            "entry_trigger_price": None,
            "pending_entry_direction_signal": None,
            "pending_sl_price": None
        })
        logger.info(f"🧹 [MONITOR] Pending order data cleaned")
        
