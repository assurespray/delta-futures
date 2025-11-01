"""Position and trade execution management with breakout entry logic."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import (
    place_market_order, 
    place_stop_market_entry_order,
    place_stop_loss_order, 
    cancel_all_orders,
    get_order_by_id
)
from api.positions import get_position_by_symbol
from api.market_data import get_product_by_symbol
from database.crud import (
    create_algo_activity, update_algo_activity, 
    update_algo_setup, get_open_activity_by_setup
)
from indicators.signal_generator import SignalGenerator
from config.settings import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """Manage breakout entries, stop-loss protection, and exits."""
    
    def __init__(self):
        """Initialize position manager."""
        self.signal_generator = SignalGenerator()
    
    async def place_breakout_entry_order(self, client: DeltaExchangeClient, 
                                        algo_setup: Dict[str, Any],
                                        entry_side: str, 
                                        breakout_price: float,
                                        sirusu_value: float,
                                        immediate: bool = False) -> bool:

        """
        Place breakout entry order (stop-market at candle high/low + 1 pip).
        ✅ FIXED: Added critical validation to prevent duplicate entries.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            entry_side: "long" or "short"
            breakout_price: Trigger price (previous candle extreme + 1 pip)
            sirusu_value: Sirusu value for stop-loss
            immediate: If True, place market order immediately
        
        Returns:
            True if successful, False otherwise
        """
        try:
            setup_id = str(algo_setup["_id"])
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            
            # ✅ CRITICAL CHECK 1: Verify NO position is already open
            current_position = algo_setup.get("current_position")
            if current_position:
                logger.error(f"❌ ENTRY REJECTED: Position already open for {symbol}")
                logger.error(f"   Current position: {current_position.upper()}")
                logger.error(f"   Requested entry: {entry_side.upper()}")
                logger.error(f"   Setup ID: {setup_id}")
                return False
            
            # ✅ CRITICAL CHECK 2: Verify NO pending entry order exists
            pending_entry_id = algo_setup.get("pending_entry_order_id")
            if pending_entry_id:
                logger.error(f"❌ ENTRY REJECTED: Pending entry order already exists for {symbol}")
                logger.error(f"   Pending order ID: {pending_entry_id}")
                logger.error(f"   Setup ID: {setup_id}")
                return False
            
            # Get product ID if not cached
            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if not product:
                    logger.error(f"❌ Product not found: {symbol}")
                    return False
                product_id = product["id"]
                await update_algo_setup(setup_id, {"product_id": product_id})

            # Determine order side
            order_side = "buy" if entry_side == "long" else "sell"

            # Cancel any existing orders first
            await cancel_all_orders(client, product_id)

            # ✅ CHECK IF IMMEDIATE EXECUTION NEEDED
            if immediate:
                logger.info(f"🎯 Placing immediate MARKET {entry_side.upper()} for {symbol}")
                logger.info(f"   Entry price: ${breakout_price:.5f}")
                logger.info(f"   Lot size: {lot_size}")
    
                entry_order = await place_market_order(
                    client, product_id, lot_size, order_side
                )
    
                if not entry_order:
                    logger.error(f"❌ Failed to place market entry order for {symbol}")
                    return False
    
                # Market orders fill immediately
                entry_price = float(entry_order.get("average_fill_price", breakout_price))
    
                logger.info(f"✅ Immediate market entry: {entry_side.upper()} @ ${entry_price:.5f}")
    
                # Create activity record immediately
                activity_data = {
                    "user_id": algo_setup["user_id"],
                    "algo_setup_id": setup_id,
                    "algo_setup_name": algo_setup["setup_name"],
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
    
                # ✅ Update algo setup - position is now open (CRITICAL!)
                await update_algo_setup(setup_id, {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "last_signal_time": datetime.utcnow()
                })
    
                # ✅ FIXED: Place stop-loss if enabled and capture order ID
                if algo_setup.get("additional_protection", False):
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sirusu_value, setup_id
                    )
                    logger.info(f"✅ Stop-loss placed with ID: {sl_order_id}")

                return True


            # Otherwise, place stop order as normal
            logger.info(f"🎯 Placing breakout {entry_side.upper()} order for {symbol}")
            logger.info(f"   Breakout trigger: ${breakout_price:.5f}")
            logger.info(f"   Lot size: {lot_size}")
            
            entry_order = await place_stop_market_entry_order(
                client, product_id, lot_size, order_side, breakout_price
            )
            
            if not entry_order:
                logger.error(f"❌ Failed to place breakout entry order for {symbol}")
                return False
            
            entry_order_id = entry_order.get("id")
            logger.info(f"✅ Breakout entry order placed: ID {entry_order_id}")
            
            # ✅ Update algo setup with pending order info
            await update_algo_setup(setup_id, {
                "pending_entry_order_id": entry_order_id,
                "entry_trigger_price": breakout_price,
                "pending_entry_direction_signal": 1 if entry_side == "long" else -1,
                "last_signal_time": datetime.utcnow()
            })
      
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception placing breakout entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def check_entry_order_filled(self, client: DeltaExchangeClient,
                                      algo_setup: Dict[str, Any],
                                      sirusu_value: float) -> bool:
        """
        Check if pending breakout entry order was filled, and set up stop-loss.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            sirusu_value: Sirusu value for stop-loss
        
        Returns:
            True if order filled and processed, False otherwise
        """
        try:
            setup_id = str(algo_setup["_id"])
            pending_order_id = algo_setup.get("pending_entry_order_id")
            
            if not pending_order_id:
                return False
            
            # Check order status
            order = await get_order_by_id(client, pending_order_id)
            
            if not order:
                logger.warning(f"⚠️ Could not retrieve order {pending_order_id}")
                return False
            
            order_state = order.get("state", "").lower()
            
            # Order filled!
            if order_state in ["filled", "closed"]:
                logger.info(f"✅ Breakout entry order FILLED: {pending_order_id}")
                
                # Get fill details
                entry_price = float(order.get("average_fill_price", 0))
                if entry_price == 0:
                    entry_price = float(order.get("stop_price", 0))
                
                entry_side = "long" if order.get("side") == "buy" else "short"
                lot_size = algo_setup["lot_size"]
                symbol = algo_setup["asset"]
                product_id = algo_setup["product_id"]
                
                logger.info(f"   Entry: {entry_side.upper()} {lot_size} @ ${entry_price:.5f}")
                
                # ✅ FIXED: Place stop-loss if additional protection enabled and capture order ID
                if algo_setup.get("additional_protection", False):
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sirusu_value, setup_id
                    )
                    logger.info(f"✅ Stop-loss placed with ID: {sl_order_id}")
                
                # Create activity record
                activity_data = {
                    "user_id": algo_setup["user_id"],
                    "algo_setup_id": setup_id,
                    "algo_setup_name": algo_setup["setup_name"],
                    "entry_time": datetime.utcnow(),
                    "entry_price": entry_price,
                    "entry_trigger_price": algo_setup.get("entry_trigger_price"),
                    "direction": entry_side,
                    "lot_size": lot_size,
                    "asset": symbol,
                    "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend",
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "is_closed": False
                }
                
                activity_id = await create_algo_activity(activity_data)
                
                # ✅ Update algo setup - CRITICAL: set current_position
                await update_algo_setup(setup_id, {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "last_signal_time": datetime.utcnow()
                })
                
                logger.info(f"✅ Trade entry recorded: Activity ID {activity_id}")
                return True
            
            # Order still pending
            elif order_state in ["open", "pending"]:
                logger.debug(f"⏳ Entry order still pending: {pending_order_id}")
                return False
            
            # Order cancelled or failed
            else:
                logger.warning(f"⚠️ Entry order {order_state}: {pending_order_id}")
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None
                })
                return False
            
        except Exception as e:
            logger.error(f"❌ Exception checking entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def _place_stop_loss_protection(self, client: DeltaExchangeClient, 
                                         product_id: int, lot_size: int, 
                                         position_side: str, stop_price: float,
                                         setup_id: Optional[str] = None) -> Optional[int]:
        """
        Place stop-loss market order for position protection (Sirusu value).
        ✅ FIXED: Returns order ID so we can track and cancel it later.
    
        Args:
            client: Delta Exchange client
            product_id: Product ID
            lot_size: Position size
            position_side: "long" or "short"
            stop_price: Stop-loss trigger price (Sirusu value)
            setup_id: Algo setup ID (optional, for database updates)
    
        Returns:
            Stop-loss order ID if successful, None otherwise
        """
        try:
            # Determine stop-loss order side (opposite of position)
            sl_side = "sell" if position_side == "long" else "buy"
        
            logger.info(f"🛡️ Placing stop-loss protection: {sl_side.upper()} @ ${stop_price:.5f}")
        
            sl_order = await place_stop_loss_order(
                client, product_id, lot_size, sl_side, stop_price, use_stop_market=True
            )
        
            if sl_order:
                sl_order_id = sl_order.get("id")
                logger.info(f"✅ Stop-loss order placed successfully (ID: {sl_order_id})")
            
                # ✅ FIXED: Save stop-loss order ID to database for later cancellation
                if setup_id:
                    await update_algo_setup(setup_id, {
                        "stop_loss_order_id": sl_order_id
                    })
                    logger.info(f"💾 Saved stop-loss order ID {sl_order_id} to database")
            
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
        """
        Execute market exit when Sirusu flips (trailing stop exit).
    
        ✅ CRITICAL FIX: Cancel stop-loss order BEFORE exiting!
    
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            sirusu_signal_text: Sirusu signal text for logging
    
        Returns:
            True if successful, False otherwise
        """
        try:
            setup_id = str(algo_setup["_id"])
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            current_position = algo_setup.get("current_position")
        
            if not current_position:
                logger.warning(f"⚠️ No current position for {symbol}")
                return False
        
            # ✅ STEP 1: CANCEL STOP-LOSS ORDER FIRST (before market exit)
            stop_loss_order_id = algo_setup.get("stop_loss_order_id")
            sl_cancelled = False
        
            if stop_loss_order_id:
                logger.info(f"🔄 [STEP 1] Attempting to cancel stop-loss order: {stop_loss_order_id}")
            
                from api.orders import cancel_order
                try:
                    cancelled = await cancel_order(client, stop_loss_order_id)
                
                    if cancelled:
                        sl_cancelled = True
                        logger.info(f"✅ Stop-loss order {stop_loss_order_id} cancelled successfully")
                    else:
                        # ✅ FIX: Log as INFO, not ERROR (404 is normal!)
                        logger.info(f"ℹ️ Stop-loss order {stop_loss_order_id} not found (likely already executed)")
                        sl_cancelled = True  # ← Treat as success - doesn't matter if it's already gone
            
                except Exception as e:
                    logger.error(f"❌ Error cancelling stop-loss: {e}")
                    # ✅ Don't return False - continue with market exit anyway!
            else:
                logger.info(f"ℹ️ [STEP 1] No stop-loss order to cancel")
                sl_cancelled = True
        
            # ✅ STEP 2: EXECUTE MARKET EXIT (close the position)
            logger.info(f"🚪 [STEP 2] Executing Sirusu exit for {symbol} {current_position.upper()}")
        
            # Determine exit order side (opposite of position)
            exit_side = "sell" if current_position == "long" else "buy"
        
            # Place market order to close position
            logger.info(f"   Direction: {current_position.upper()}")
            logger.info(f"   Exit reason: {sirusu_signal_text}")
            logger.info(f"   Market order: {exit_side.upper()} {lot_size} contracts")
        
            order = await place_market_order(client, product_id, lot_size, exit_side)
        
            if not order:
                logger.error(f"❌ Failed to place exit order for {symbol}")
                return False
        
            exit_price = float(order.get("average_fill_price", 0))
            if exit_price == 0:
                exit_price = float(order.get("limit_price", 0))
        
            logger.info(f"✅ Market exit executed: Close {current_position.upper()} @ ${exit_price:.5f}")
        
            # ✅ STEP 3: RECORD EXIT ACTIVITY & UPDATE DATABASE
            logger.info(f"💾 [STEP 3] Recording exit activity...")
        
            # Get open activity record
            activity = await get_open_activity_by_setup(setup_id)
        
            if activity:
                # Calculate PnL
                entry_price = activity.get("entry_price", 0)
                pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position)
                pnl_inr = pnl * settings.usd_to_inr_rate
            
                # Update activity record
                update_data = {
                    "exit_time": datetime.utcnow(),
                    "exit_price": exit_price,
                    "pnl": round(pnl, 4),
                    "pnl_inr": round(pnl_inr, 2),
                    "sirusu_exit_signal": sirusu_signal_text,
                    "is_closed": True
                }
            
                await update_algo_activity(str(activity["_id"]), update_data)
            
                logger.info(f"💰 Trade PnL: ${pnl:.4f} (₹{pnl_inr:.2f})")
            
            # ✅ STEP 4: RESET ALGO SETUP STATE TO WAITING
            logger.info(f"🔄 [STEP 4] Resetting bot state...")
        
            await update_algo_setup(setup_id, {
                "current_position": None,           # ← Clear position
                "last_entry_price": None,           # ← Clear entry price
                "pending_entry_order_id": None,     # ← Clear pending entry
                "entry_trigger_price": None,        # ← Clear trigger price
                "stop_loss_order_id": None,         # ← CRITICAL: Clear stop-loss ID!
                "last_signal_time": datetime.utcnow()
            })
        
            logger.info(f"✅ Position closed - Bot back to WAITING state")
            logger.info(f"=" * 60)
        
            return True
        
        except Exception as e:
            logger.error(f"❌ Exception executing exit: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
            
    def _calculate_pnl(self, entry_price: float, exit_price: float, 
                      lot_size: int, position_side: str) -> float:
        """
        Calculate profit/loss for a trade.
        
        Args:
            entry_price: Entry price
            exit_price: Exit price
            lot_size: Number of contracts
            position_side: "long" or "short"
        
        Returns:
            PnL in USD
        """
        if position_side == "long":
            pnl = (exit_price - entry_price) * lot_size
        else:  # short
            pnl = (entry_price - exit_price) * lot_size
        
        return pnl
                
