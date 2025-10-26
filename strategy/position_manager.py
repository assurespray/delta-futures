"""Position and trade execution management."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import place_market_order, place_stop_loss_order, cancel_all_orders
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
    """Manage trade execution, entries, and exits."""
    
    def __init__(self):
        """Initialize position manager."""
        self.signal_generator = SignalGenerator()
    
    async def execute_entry(self, client: DeltaExchangeClient, algo_setup: Dict[str, Any],
                           entry_side: str, sirusu_value: float) -> bool:
        """
        Execute entry trade with optional stop-loss protection.
        
        Args:
            client: Delta Exchange client
            algo_setup: Algo setup configuration
            entry_side: "long" or "short"
            sirusu_value: Sirusu value for stop-loss
        
        Returns:
            True if successful, False otherwise
        """
        try:
            setup_id = str(algo_setup["_id"])
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            product_id = algo_setup.get("product_id")
            
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
            
            # Place market order
            logger.info(f"📊 Executing {entry_side.upper()} entry for {symbol}, size: {lot_size}")
            order = await place_market_order(client, product_id, lot_size, order_side)
            
            if not order:
                logger.error(f"❌ Failed to place entry order for {symbol}")
                return False
            
            entry_price = float(order.get("average_fill_price", 0))
            if entry_price == 0:
                entry_price = float(order.get("limit_price", 0))
            
            logger.info(f"✅ Entry order executed: {entry_side.upper()} {lot_size} @ ${entry_price}")
            
            # Place stop-loss if additional protection enabled
            if algo_setup.get("additional_protection", False):
                await self._place_stop_loss_protection(
                    client, product_id, lot_size, entry_side, sirusu_value
                )
            
            # Create activity record
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
            
            activity_id = await create_algo_activity(activity_data)
            
            # Update algo setup
            await update_algo_setup(setup_id, {
                "current_position": entry_side,
                "last_entry_price": entry_price,
                "last_signal_time": datetime.utcnow()
            })
            
            logger.info(f"✅ Trade entry recorded: Activity ID {activity_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception executing entry: {e}")
            return False
    
    async def _place_stop_loss_protection(self, client: DeltaExchangeClient, product_id: int,
                                         lot_size: int, position_side: str, stop_price: float) -> bool:
        """
        Place stop-loss limit order for position protection.
        
        Args:
            client: Delta Exchange client
            product_id: Product ID
            lot_size: Position size
            position_side: "long" or "short"
            stop_price: Stop-loss trigger price
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Determine stop-loss order side
            sl_side = self.signal_generator.get_stop_loss_side(position_side)
            
            logger.info(f"🛡️ Placing stop-loss protection: {sl_side.upper()} @ ${stop_price}")
            
            sl_order = await place_stop_loss_order(
                client, product_id, lot_size, sl_side, stop_price
            )
            
            if sl_order:
                logger.info(f"✅ Stop-loss order placed successfully")
                return True
            else:
                logger.warning(f"⚠️ Failed to place stop-loss order")
                return False
            
        except Exception as e:
            logger.error(f"❌ Exception placing stop-loss: {e}")
            return False
    
    async def execute_exit(self, client: DeltaExchangeClient, algo_setup: Dict[str, Any],
                          sirusu_signal_text: str) -> bool:
        """
        Execute exit trade and update records.
        
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
            
            # Cancel any existing orders (including stop-loss)
            await cancel_all_orders(client, product_id)
            
            # Determine exit order side (opposite of position)
            exit_side = "sell" if current_position == "long" else "buy"
            
            # Place market order to close position
            logger.info(f"📊 Executing exit for {symbol} {current_position.upper()}, size: {lot_size}")
            order = await place_market_order(client, product_id, lot_size, exit_side)
            
            if not order:
                logger.error(f"❌ Failed to place exit order for {symbol}")
                return False
            
            exit_price = float(order.get("average_fill_price", 0))
            if exit_price == 0:
                exit_price = float(order.get("limit_price", 0))
            
            logger.info(f"✅ Exit order executed: Close {current_position.upper()} @ ${exit_price}")
            
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
                    "pnl": round(pnl, 2),
                    "pnl_inr": round(pnl_inr, 2),
                    "sirusu_exit_signal": sirusu_signal_text,
                    "is_closed": True
                }
                
                await update_algo_activity(str(activity["_id"]), update_data)
                
                logger.info(f"💰 PnL: ${pnl:.2f} (₹{pnl_inr:.2f})")
            
            # Update algo setup
            await update_algo_setup(setup_id, {
                "current_position": None,
                "last_entry_price": None,
                "last_signal_time": datetime.utcnow()
            })
            
            logger.info(f"✅ Trade exit recorded and position cleared")
            return True
            
        except Exception as e:
            logger.error(f"❌ Exception executing exit: {e}")
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
                                             
