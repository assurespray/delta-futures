"""Position and trade execution management with breakout entry logic + Asset Lock."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import (
    place_market_order, 
    place_stop_market_entry_order,
    place_stop_loss_order, 
    cancel_all_orders,
    get_order_by_id,
    cancel_order
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

logger = logging.getLogger(__name__)


class PositionManager:
    """Manage breakout entries, stop-loss protection, and exits with asset locking."""
    
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
        Place breakout entry order with ASSET LOCK protection.
        ✅ ENHANCED: Prevents multi-timeframe conflicts
        
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
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            
            logger.info(f"=" * 70)
            logger.info(f"🚀 PLACING ENTRY ORDER")
            logger.info(f"=" * 70)
            logger.info(f"Setup: {setup_name}")
            logger.info(f"Asset: {symbol}")
            logger.info(f"Direction: {entry_side.upper()}")
            
            # ✅ CHECK 1: THIS setup doesn't have position
            current_position = algo_setup.get("current_position")
            if current_position:
                logger.error(f"❌ ENTRY REJECTED: {setup_name} already has {current_position.upper()} position")
                logger.error(f"=" * 70)
                return False
            
            # ✅ CHECK 2: NO pending entry order
            pending_entry_id = algo_setup.get("pending_entry_order_id")
            if pending_entry_id:
                logger.error(f"❌ ENTRY REJECTED: Pending entry order already exists")
                logger.error(f"   Order ID: {pending_entry_id}")
                logger.error(f"=" * 70)
                return False
            
            # ✅ CHECK 3: ACQUIRE GLOBAL ASSET LOCK
            logger.info(f"🔐 Attempting to acquire lock on {symbol}...")
            
            db = await get_db()
            
            lock_acquired = await acquire_position_lock(
                db, symbol, setup_id, setup_name
            )
            
            if not lock_acquired:
                logger.error(f"❌ ENTRY REJECTED: {symbol} is already traded by another setup")
                
                # Log which setup owns it
                lock = await get_position_lock(db, symbol)
                if lock:
                    logger.error(f"   Conflicting setup: {lock['setup_name']}")
                
                logger.error(f"=" * 70)
                return False
            
            logger.info(f"✅ Lock acquired on {symbol}")
            
            # ✅ CHECK 4: Verify NO position on exchange
            logger.info(f"🔍 Verifying no position on exchange...")
            
            actual_position = await get_position_by_symbol(client, symbol)
            actual_size = actual_position.get("size", 0) if actual_position else 0
            
            if actual_size != 0:
                logger.error(f"❌ ENTRY REJECTED: {symbol} has {actual_size} contracts on exchange!")
                
                # Release lock immediately
                await release_position_lock(db, symbol, setup_id)
                
                logger.error(f"=" * 70)
                return False
            
            logger.info(f"✅ Exchange position verified: clear")
            
            # Get product ID if not cached
            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if not product:
                    logger.error(f"❌ Product not found: {symbol}")
                    await release_position_lock(db, symbol, setup_id)
                    logger.error(f"=" * 70)
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
                    logger.error(f"❌ Failed to place market entry order")
                    await release_position_lock(db, symbol, setup_id)
                    logger.error(f"=" * 70)
                    return False
    
                # Market orders fill immediately
                entry_price = float(entry_order.get("average_fill_price", breakout_price))
    
                logger.info(f"✅ Immediate market entry: {entry_side.upper()} @ ${entry_price:.5f}")
    
                # Create activity record immediately
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
    
                # ✅ Update algo setup - position is now open + LOCK HELD
                await update_algo_setup(setup_id, {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "last_signal_time": datetime.utcnow(),
                    "position_lock_acquired": True
                })
    
                # ✅ FIXED: Place stop-loss if enabled and capture order ID
                if algo_setup.get("additional_protection", False):
                    sl_order_id = await self._place_stop_loss_protection(
                        client, product_id, lot_size, entry_side, sirusu_value, setup_id
                    )
                    logger.info(f"✅ Stop-loss placed with ID: {sl_order_id}")
                
                logger.info(f"=" * 70)
                return True

            # Otherwise, place stop order as normal
            logger.info(f"🎯 Placing breakout {entry_side.upper()} order for {symbol}")
            logger.info(f"   Breakout trigger: ${breakout_price:.5f}")
            logger.info(f"   Lot size: {lot_size}")
            
            entry_order = await place_stop_market_entry_order(
                client, product_id, lot_size, order_side, breakout_price
            )
            
            if not entry_order:
                logger.error(f"❌ Failed to place breakout entry order")
                await release_position_lock(db, symbol, setup_id)
                logger.error(f"=" * 70)
                return False
            
            entry_order_id = entry_order.get("id")
            logger.info(f"✅ Breakout entry order placed: ID {entry_order_id}")
            
            # ✅ Update algo setup with LOCK AND pending order
            await update_algo_setup(setup_id, {
                "pending_entry_order_id": entry_order_id,
                "entry_trigger_price": breakout_price,
                "pending_entry_direction_signal": 1 if entry_side == "long" else -1,
                "last_signal_time": datetime.utcnow(),
                "position_lock_acquired": True
            })
            
            logger.info(f"✅ Entry setup complete - lock held, order pending")
            logger.info(f"=" * 70)
      
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception placing breakout entry order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Attempt to release lock on exception
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
                
                # ✅ Update algo setup - CRITICAL: set current_position + LOCK HELD
                await update_algo_setup(setup_id, {
                    "current_position": entry_side,
                    "last_entry_price": entry_price,
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "last_signal_time": datetime.utcnow(),
                    "position_lock_acquired": True
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
                
                # Release lock if order cancelled/failed
                db = await get_db()
                symbol = algo_setup["asset"]
                await release_position_lock(db, symbol, setup_id)
                
                await update_algo_setup(setup_id, {
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "position_lock_acquired": False
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
        ✅ SIMPLIFIED: Execute market exit when Sirusu flips.
    
        Logic:
        1. Check if position exists on exchange (with retry)
        2. If NO position → Skip exit (already closed)
        3. If position exists → Exit at market → Cancel orphaned stop-loss
        """
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            current_position = algo_setup.get("current_position")
            stop_loss_order_id = algo_setup.get("stop_loss_order_id")
        
            if not current_position:
                logger.warning(f"⚠️ No current position for {symbol} in database")
                return False
        
            logger.info(f"=" * 70)
            logger.info(f"🚪 EXECUTING EXIT SIGNAL")
            logger.info(f"=" * 70)
            logger.info(f"Setup: {setup_name}")
            logger.info(f"Asset: {symbol}")
            logger.info(f"Position: {current_position.upper()}")
            logger.info(f"Trigger: {sirusu_signal_text}")
        
            # ✅ STEP 1: Check if position exists (with 3 retries)
            logger.info(f"🔍 [STEP 1] Verifying position on exchange...")
        
            actual_position = None
            import asyncio
        
            for attempt in range(3):
                actual_position = await get_position_by_symbol(client, symbol)
                if actual_position:
                    break
                if attempt < 2:
                    logger.warning(f"   Retry {attempt + 1}/3...")
                    await asyncio.sleep(0.5)
        
            actual_size = actual_position.get("size", 0) if actual_position else 0
        
            # ✅ SCENARIO A: Position already closed
            if actual_size == 0:
                logger.warning(f"ℹ️ POSITION ALREADY CLOSED!")
                logger.warning(f"   Position was closed by stop-loss or manually")
                logger.warning(f"   Skipping market exit, cleaning up database...")
            
                # Update database - mark as closed
                activity = await get_open_activity_by_setup(setup_id)
                if activity:
                    await update_algo_activity(str(activity["_id"]), {
                        "exit_time": datetime.utcnow(),
                        "sirusu_exit_signal": f"Position already closed ({sirusu_signal_text})",
                        "is_closed": True
                    })
            
                # Clear position state
                await update_algo_setup(setup_id, {
                    "current_position": None,
                    "last_entry_price": None,
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "stop_loss_order_id": None,
                    "position_lock_acquired": False
                })
                
                # Release lock
                db = await get_db()
                await release_position_lock(db, symbol, setup_id)
            
                logger.info(f"✅ Database cleaned up, lock released")
                logger.info(f"=" * 70)
                return True
        
            # ✅ SCENARIO B: Position exists - execute market exit
            logger.info(f"✅ Position verified: {actual_size} contracts")
            logger.info(f"   Entry: ${actual_position.get('entry_price', 0)}")
            logger.info(f"   Current: ${actual_position.get('mark_price', 0)}")
            logger.info(f"   PnL: ${actual_position.get('unrealized_pnl', 0)}")
        
            # ✅ STEP 2: Place market exit order
            logger.info(f"📊 [STEP 2] Placing MARKET EXIT...")
        
            exit_side = "sell" if current_position == "long" else "buy"
        
            try:
                logger.info(f"   Placing: {exit_side.upper()} {lot_size} @ market")
            
                order = await place_market_order(client, product_id, lot_size, exit_side)
            
                if not order:
                    logger.error(f"❌ Market exit order failed!")
                    return False
            
                exit_price = float(order.get("average_fill_price", 0))
                if exit_price == 0:
                    exit_price = float(order.get("limit_price", 0))
            
                logger.info(f"✅ Position CLOSED @ ${exit_price:.5f}")
            
            except Exception as e:
                error_msg = str(e).lower()
            
                if "no_position" in error_msg or "reduce_only" in error_msg:
                    logger.warning(f"⚠️ Position already closed during exit attempt")
                    exit_price = 0.0
                else:
                    logger.error(f"❌ Market exit error: {e}")
                    return False
        
            # ✅ STEP 3: Cancel orphaned stop-loss order
            logger.info(f"🔄 [STEP 3] Cancelling orphaned stop-loss...")
        
            if stop_loss_order_id:
                try:
                    logger.info(f"   Cancelling SL order: {stop_loss_order_id}")
                
                    result = await cancel_order(client, stop_loss_order_id)
                
                    if result:
                        logger.info(f"✅ Stop-loss cancelled successfully")
                    else:
                        logger.info(f"ℹ️ Stop-loss already gone")
                    
                except Exception as e:
                    error_msg = str(e).lower()
                
                    if "404" in error_msg or "not found" in error_msg:
                        logger.info(f"ℹ️ Stop-loss already executed/gone (404)")
                    else:
                        logger.warning(f"⚠️ SL cancellation issue: {e}")
            else:
                logger.info(f"ℹ️ No stop-loss order to cancel")
        
            # ✅ STEP 4: Record exit & PnL
            logger.info(f"💾 [STEP 4] Recording exit activity...")
        
            activity = await get_open_activity_by_setup(setup_id)
        
            if activity and exit_price > 0:
                entry_price = activity.get("entry_price", 0)
                pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position)
                pnl_inr = pnl * settings.usd_to_inr_rate
            
                logger.info(f"💰 PnL: ${pnl:.4f} (₹{pnl_inr:.2f})")
            
                await update_algo_activity(str(activity["_id"]), {
                    "exit_time": datetime.utcnow(),
                    "exit_price": exit_price,
                    "pnl": round(pnl, 4),
                    "pnl_inr": round(pnl_inr, 2),
                    "sirusu_exit_signal": sirusu_signal_text,
                    "is_closed": True
                })
        
            # ✅ STEP 5: Reset state and release lock
            logger.info(f"🔄 [STEP 5] Resetting bot state...")
        
            await update_algo_setup(setup_id, {
                "current_position": None,
                "last_entry_price": None,
                "pending_entry_order_id": None,
                "entry_trigger_price": None,
                "stop_loss_order_id": None,
                "position_lock_acquired": False
            })
        
            # Release lock
            db = await get_db()
            await release_position_lock(db, symbol, setup_id)
        
            logger.info(f"✅ Lock released - trade complete")
            logger.info(f"=" * 70)
            logger.info(f"✅ TRADE EXIT COMPLETE")
            if activity:
                logger.info(f"   Entry: ${activity.get('entry_price', 0):.5f}")
            if exit_price > 0:
                logger.info(f"   Exit: ${exit_price:.5f}")
            logger.info(f"   Reason: {sirusu_signal_text}")
            logger.info(f"=" * 70)
        
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception in execute_exit: {e}")
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
