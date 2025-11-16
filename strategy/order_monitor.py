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
        client: DeltaExchangeClient,
        algo_setup: Dict[str, Any],
        current_perusu_signal: int,
        current_sirusu_signal: int,  # ← ADD THIS
        sirusu_value: float,
        logger_bot: LoggerBot
    ) -> Optional[str]:
        """
        Check pending entry order status and handle reversals.
    
        ✅ NEW: Cancels order if SIRUSU flips (not Perusu)
    
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            current_perusu_signal: Current Perusu signal (1 or -1)
            current_sirusu_signal: Current Sirusu signal (1 or -1)  # ← NEW
            sirusu_value: Current Sirusu value
            logger_bot: Logger bot for notifications
    
        Returns:
            Order status: "filled", "reversed", "pending", or None
        """
        pending_order_id = algo_setup.get('pending_entry_order_id')
    
        if not pending_order_id:
            return None
    
        setup_id = str(algo_setup['_id'])
        setup_name = algo_setup['setup_name']
        pending_side = algo_setup.get('pending_entry_side')
    
        logger.info(f"📋 Checking pending {pending_side} entry order: {pending_order_id}")
    
        try:
            # Get order details from exchange
            order = await client.get_order_by_id(pending_order_id)
        
            if not order:
                logger.warning(f"⚠️ Pending order {pending_order_id} not found")
                # Clean up DB
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "pending_entry_side": None
                })
                return None
        
            order_state = order.get('state', '').upper()
            logger.info(f"   Order state: {order_state}")
            
            # ===== CHECK 1: Order Filled =====
            if order_state == 'FILLED':
                logger.info(f"✅ Pending {pending_side} entry order FILLED")
            
                # Get fill details
                fill_price = float(order.get('average_fill_price', 0))
                size = float(order.get('size', 0))
            
                # ✅ CRITICAL: Update position in DB
                position_id = await self.position_manager.record_position_opened(
                    algo_setup_id=setup_id,
                    order_id=order.get('id'),
                    product_id=order.get('product_id'),
                    symbol=order.get('symbol'),
                    side=pending_side,
                    size=size,
                    entry_price=fill_price,
                    stop_loss_price=sirusu_value
                )
            
                # Update algo setup
                await update_algo_setup(setup_id, {
                    "current_position": pending_side,
                    "pending_entry_order_id": None,
                    "pending_entry_side": None,
                    "position_id": position_id
                })
            
                # Send notification
                await logger_bot.send_trade_entry(
                    setup_name=setup_name,
                    asset=algo_setup['asset'],
                    direction=pending_side,
                    entry_price=fill_price,
                    lot_size=size,
                    perusu_signal="Breakout filled",
                    sirusu_sl=sirusu_value
                )
                
                return "filled"
        
            # ===== CHECK 2: Order Cancelled =====
            elif order_state in ['CANCELLED', 'REJECTED']:
                logger.info(f"ℹ️ Pending order was {order_state}")
            
                # Clean up DB
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "pending_entry_side": None
                })
                
                return "cancelled"
        
            # ===== CHECK 3: Order Still Open → Check for Sirusu Reversal =====
            elif order_state == 'OPEN':
                # ✅ NEW LOGIC: Check SIRUSU flip (not Perusu)
            
                # Get cached Sirusu signal from when order was placed
                cached_sirusu = await get_indicator_cache(setup_id, "sirusu")
                last_sirusu_signal = cached_sirusu.get('last_signal') if cached_sirusu else None
            
                if last_sirusu_signal is None:
                    logger.warning(f"⚠️ No cached Sirusu signal - cannot check reversal")
                    return "pending"
            
                # ===== CRITICAL: Check if Sirusu flipped =====
                sirusu_flipped = False
                
                if pending_side == "long":
                    # For LONG entry: Cancel if Sirusu flips to Downtrend
                    if current_sirusu_signal == -1 and last_sirusu_signal == 1:
                        sirusu_flipped = True
                        logger.warning(f"🔄 SIRUSU REVERSAL: Uptrend → Downtrend")
            
                elif pending_side == "short":
                    # For SHORT entry: Cancel if Sirusu flips to Uptrend
                    if current_sirusu_signal == 1 and last_sirusu_signal == -1:
                        sirusu_flipped = True
                        logger.warning(f"🔄 SIRUSU REVERSAL: Downtrend → Uptrend")
                    
                # ===== If Sirusu flipped, cancel the order =====
                if sirusu_flipped:
                    logger.warning(f"❌ Cancelling pending {pending_side} order - Sirusu signal reversed, trade invalid")
                
                    # Cancel order on exchange
                    cancel_success = await client.cancel_order(pending_order_id)
                
                    if cancel_success:
                        logger.info(f"✅ Order {pending_order_id} cancelled successfully")
                    
                        # Update DB
                        await update_algo_setup(setup_id, {
                            "pending_entry_order_id": None,
                            "pending_entry_side": None
                        })
                    
                        # Send notification
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
        
            else:
                logger.warning(f"⚠️ Unknown order state: {order_state}")
                return None
    
        except Exception as e:
            logger.error(f"❌ Error checking pending order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
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
        
