"""Pending order monitoring and management."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import get_order_by_id, cancel_order, place_stop_loss_order
from database.crud import update_algo_setup, create_algo_activity

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
        Check pending entry order status (robust: using open+history only).
        Cancels on SIRUSU reversal.

        Returns: "filled", "reversed", "pending", or None
        """
        pending_order_id = algo_setup.get('pending_entry_order_id')
        if not pending_order_id:
            return None

        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        pending_side = algo_setup.get('pending_entry_side')
        product_id = algo_setup.get('product_id')

        logger.info(f"📋 Checking pending {pending_side} entry order: {pending_order_id}")

        try:
            # Robust order state lookup
            # Use only open orders and order history!
            is_filled_or_gone = await is_order_gone(client, pending_order_id, product_id)

            if is_filled_or_gone:
                logger.info(f"✅ Pending {pending_side} entry order FILLED or CANCELLED (gone from orderbook)")
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "pending_entry_side": None
                })
                # You probably want to handle the "filled" case with follow-up DB/activity logic here
                # (see _handle_filled_order, but with fresh history fetch if needed)
                return "filled"

            # Still live - get last placed Sirusu signal for reversal logic
            cached_sirusu = await get_indicator_cache(setup_id, "sirusu")
            last_sirusu_signal = cached_sirusu.get('last_signal') if cached_sirusu else None

            if last_sirusu_signal is None:
                logger.warning(f"⚠️ No cached Sirusu signal - cannot check reversal")
                return "pending"

            sirusu_flipped = False
            if pending_side == "long":
                if current_sirusu_signal == -1 and last_sirusu_signal == 1:
                    sirusu_flipped = True
                    logger.warning(f"🔄 SIRUSU REVERSAL: Uptrend → Downtrend")
            elif pending_side == "short":
                if current_sirusu_signal == 1 and last_sirusu_signal == -1:
                    sirusu_flipped = True
                    logger.warning(f"🔄 SIRUSU REVERSAL: Downtrend → Uptrend")

            if sirusu_flipped:
                logger.warning(f"❌ Cancelling pending {pending_side} order - Sirusu signal reversed")
                cancel_success = await cancel_order(client, pending_order_id)
                if cancel_success:
                    await update_algo_setup(setup_id, {
                        "pending_entry_order_id": None,
                        "pending_entry_side": None
                    })
                    await logger_bot.send_info(
                        f"⚠️ {setup_name}: {pending_side.upper()} entry cancelled\n"
                        f"Reason: Sirusu signal reversed - trade no longer valid"
                    )
                    return "reversed"
                else:
                    logger.error(f"❌ Failed to cancel order {pending_order_id}")
                    return "pending"
            else:
                logger.info(f"⏳ Order still pending - Sirusu has not reversed")
                logger.info(f"   Last Sirusu: {last_sirusu_signal}, Current: {current_sirusu_signal}")
                return "pending"

        except Exception as e:
            logger.error(f"❌ Error checking pending order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

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
        entry_price = float(order.get("average_fill_price", 0))
        if entry_price == 0:
            entry_price = float(order.get("stop_price", 0))
        
        entry_side = "long" if order.get("side") == "buy" else "short"
        lot_size = algo_setup['lot_size']
        
        logger.info(f"   Entry: {entry_side.upper()} {lot_size} @ ${entry_price:.5f}")
        
        # Place stop-loss if enabled
        if algo_setup.get("additional_protection", False):
            await self._place_stop_loss(
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
            "entry_trigger_price": None,
            "pending_entry_direction_signal": None,
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
        cancelled = await cancel_order(client, pending_order_id)
        
        if cancelled:
            logger.info(f"✅ [MONITOR] Pending order cancelled successfully")
            
            # Clear pending order from database
            await update_algo_setup(setup_id, {
                "pending_entry_order_id": None,
                "entry_trigger_price": None,
                "pending_entry_direction_signal": None
            })
            
            # Send Telegram notification
            if logger_bot:
                old_signal_text = "Uptrend" if old_signal == 1 else "Downtrend"
                new_signal_text = "Uptrend" if new_signal == 1 else "Downtrend"
                await logger_bot.send_order_cancelled(
                    setup_name, old_signal_text, new_signal_text
                )
        else:
            logger.error(f"❌ [MONITOR] Failed to cancel pending order")
    
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
            logger.info(f"✅ [MONITOR] Stop-loss placed (ID: {sl_order.get('id')})")
        else:
            logger.warning(f"⚠️ [MONITOR] Failed to place stop-loss")
    
    async def _clean_pending_order(self, setup_id: str):
        """
        Clean up pending order data from database.
        
        Args:
            setup_id: Algo setup ID
        """
        await update_algo_setup(setup_id, {
            "pending_entry_order_id": None,
            "entry_trigger_price": None,
            "pending_entry_direction_signal": None
        })
        logger.info(f"🧹 [MONITOR] Pending order data cleaned")
        
