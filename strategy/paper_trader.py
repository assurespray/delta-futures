"""
Paper Trading Engine - Virtual Exchange Simulator.

Fully modular: delete this file and remove routing `if` statements
in position_manager.py to completely remove paper trading.

Features:
- Realistic slippage (uses live market price, not trigger price)
- Margin & leverage tracking
- Simulated taker fees (0.05%)
- Liquidation detection
- Virtual balance management
- SL monitoring via price polling
"""
import logging
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from api.market_data import get_latest_price, get_product_by_symbol
from database.crud import (
    create_algo_activity, update_algo_activity,
    update_algo_setup, update_screener_setup,
    get_open_activity_by_setup,
    get_paper_balance, update_paper_balance, get_db
)
from config.settings import settings
from config.constants import (
    PAPER_TRADE_TAKER_FEE,
    PAPER_TRADE_DEFAULT_LEVERAGE,
    PAPER_TRADE_DEFAULT_BALANCE,
    ENABLE_DEMO_MODE,
)

logger = logging.getLogger(__name__)


def is_paper_trade(algo_setup: Dict[str, Any]) -> bool:
    """Check if a setup should be treated as a paper trade.
    
    Returns True if:
    - The setup has is_paper_trade=True, OR
    - The global ENABLE_DEMO_MODE flag is ON
    """
    if ENABLE_DEMO_MODE:
        return True
    return algo_setup.get("is_paper_trade", False)


def _is_screener_setup(algo_setup: Dict[str, Any]) -> bool:
    """Check if a setup dict originated from the screener_setups collection."""
    return "asset_selection_type" in algo_setup


async def _update_setup_state(setup_id: str, update_data: dict, algo_setup: Optional[Dict[str, Any]] = None) -> bool:
    """Route setup update to the correct collection (algo_setups or screener_setups).
    
    If algo_setup is provided, uses it to detect the collection.
    Otherwise, tries algo_setups first, then screener_setups.
    """
    if algo_setup and _is_screener_setup(algo_setup):
        return await update_screener_setup(setup_id, update_data)
    
    # Try algo_setups first
    result = await update_algo_setup(setup_id, update_data)
    if result:
        return True
    # Fallback: might be a screener setup
    return await update_screener_setup(setup_id, update_data)


async def _get_setup_by_id(setup_id: str) -> Optional[Dict[str, Any]]:
    """Look up a setup by ID, checking both algo_setups and screener_setups collections."""
    from database.crud import get_algo_setup_by_id, get_screener_setup_by_id
    setup = await get_algo_setup_by_id(setup_id)
    if setup:
        return setup
    return await get_screener_setup_by_id(setup_id)


class PaperTrader:
    """Virtual exchange engine for paper trading."""
    
    def __init__(self):
        # Track virtual pending entries: {setup_id: {trigger_price, side, ...}}
        self._pending_entries: Dict[str, Dict[str, Any]] = {}
        # Track virtual stop-losses: {setup_id: {sl_price, side, ...}}
        self._active_stop_losses: Dict[str, Dict[str, Any]] = {}
    
    # ==================== ENTRY ====================
    
    async def place_virtual_entry(
        self,
        client,
        algo_setup: Dict[str, Any],
        entry_side: str,
        breakout_price: float,
        sirusu_value: float,
        immediate: bool = False
    ) -> bool:
        """Place a virtual entry order (paper trade equivalent of place_breakout_entry_order)."""
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            user_id = algo_setup.get("user_id", "")
            leverage = algo_setup.get("paper_leverage") or PAPER_TRADE_DEFAULT_LEVERAGE
            
            # Check if already in a position
            current_position = algo_setup.get("current_position")
            if current_position:
                logger.error(f"[PAPER] ENTRY REJECTED: {setup_name} already has {current_position.upper()}")
                return False
            
            pending_entry_id = algo_setup.get("pending_entry_order_id")
            if pending_entry_id:
                logger.error(f"[PAPER] ENTRY REJECTED: Pending entry already exists")
                return False
            
            # Get live price for margin calculation
            live_price = await get_latest_price(client, symbol)
            if not live_price:
                logger.error(f"[PAPER] Cannot get live price for {symbol}")
                return False
            
            # Calculate margin requirement
            margin_required = (live_price * lot_size) / leverage
            entry_fee = live_price * lot_size * PAPER_TRADE_TAKER_FEE
            total_cost = margin_required + entry_fee
            
            # Check virtual balance
            paper_bal = await get_paper_balance(user_id)
            if not paper_bal:
                logger.error(f"[PAPER] Cannot get paper balance for user {user_id}")
                return False
            
            available = paper_bal["balance"] - paper_bal.get("locked_margin", 0)
            if total_cost > available:
                logger.error(
                    f"[PAPER] INSUFFICIENT MARGIN: Need ${total_cost:.2f} "
                    f"(margin ${margin_required:.2f} + fee ${entry_fee:.2f}), "
                    f"available ${available:.2f}"
                )
                return False
            
            # Resolve product_id for consistency
            product_id = algo_setup.get("product_id")
            if not product_id:
                product = await get_product_by_symbol(client, symbol)
                if product:
                    product_id = product["id"]
                    await _update_setup_state(setup_id, {"product_id": product_id}, algo_setup)
            
            if immediate:
                # Execute immediately at live market price (slippage-realistic)
                return await self._execute_virtual_fill(
                    client=client,
                    algo_setup=algo_setup,
                    entry_side=entry_side,
                    fill_price=live_price,
                    sirusu_value=sirusu_value,
                    leverage=leverage,
                    margin_required=margin_required,
                    entry_fee=entry_fee
                )
            else:
                # Store as pending virtual entry (breakout order)
                paper_order_id = int(time.time() * 1000)  # Fake order ID
                
                self._pending_entries[setup_id] = {
                    "order_id": paper_order_id,
                    "symbol": symbol,
                    "side": entry_side,
                    "trigger_price": breakout_price,
                    "lot_size": lot_size,
                    "sirusu_value": sirusu_value,
                    "leverage": leverage,
                    "user_id": user_id,
                    "setup_name": setup_name,
                    "margin_locked": margin_required,  # Store actual locked amount
                    "created_at": datetime.utcnow()
                }
                
                # Lock margin for pending order
                new_locked = paper_bal.get("locked_margin", 0) + margin_required
                await update_paper_balance(user_id, {"locked_margin": new_locked})
                
                # Update setup state (include asset for screener reboot recovery)
                await _update_setup_state(setup_id, {
                    "pending_entry_order_id": paper_order_id,
                    "entry_trigger_price": breakout_price,
                    "pending_entry_side": entry_side,
                    "pending_entry_direction_signal": 1 if entry_side == "long" else -1,
                    "pending_sl_price": sirusu_value,
                    "asset": symbol,
                }, algo_setup)
                
                logger.info(
                    f"[PAPER] Breakout order placed: {entry_side.upper()} {symbol} "
                    f"@ ${breakout_price:.5f} (margin: ${margin_required:.2f})"
                )
                return True
                
        except Exception as e:
            logger.error(f"[PAPER] Exception in place_virtual_entry: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def _execute_virtual_fill(
        self,
        client,
        algo_setup: Dict[str, Any],
        entry_side: str,
        fill_price: float,
        sirusu_value: float,
        leverage: int,
        margin_required: float,
        entry_fee: float
    ) -> bool:
        """Execute a virtual trade fill at the given price."""
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            user_id = algo_setup.get("user_id", "")
            
            # Calculate liquidation price
            if entry_side == "long":
                liquidation_price = fill_price * (1 - 1 / leverage)
            else:
                liquidation_price = fill_price * (1 + 1 / leverage)
            
            # Update paper balance (lock margin, deduct fee)
            paper_bal = await get_paper_balance(user_id)
            if paper_bal:
                new_balance = paper_bal["balance"] - entry_fee
                new_locked = paper_bal.get("locked_margin", 0) + margin_required
                new_fees = paper_bal.get("total_fees", 0) + entry_fee
                await update_paper_balance(user_id, {
                    "balance": new_balance,
                    "locked_margin": new_locked,
                    "total_fees": new_fees,
                })
            
            # Create activity record
            activity_data = {
                "user_id": user_id,
                "algo_setup_id": setup_id,
                "algo_setup_name": f"[PAPER] {setup_name}",
                "entry_time": datetime.utcnow(),
                "entry_price": fill_price,
                "direction": entry_side,
                "lot_size": lot_size,
                "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend",
                "asset": symbol,
                "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                "entry_trigger_price": fill_price,
                "is_paper_trade": True,
                "paper_leverage": leverage,
                "paper_margin_used": margin_required,
                "paper_fees": entry_fee,
                "paper_liquidation_price": liquidation_price,
            }
            await create_algo_activity(activity_data)
            
            # Register virtual stop-loss for monitoring
            self._active_stop_losses[setup_id] = {
                "symbol": symbol,
                "side": entry_side,
                "sl_price": sirusu_value,
                "entry_price": fill_price,
                "lot_size": lot_size,
                "leverage": leverage,
                "liquidation_price": liquidation_price,
                "margin_used": margin_required,
                "user_id": user_id,
            }
            
            # Update setup state
            await _update_setup_state(setup_id, {
                "current_position": entry_side,
                "last_entry_price": fill_price,
                "last_signal_time": datetime.utcnow(),
                "pending_entry_order_id": None,
                "entry_trigger_price": None,
                "pending_entry_side": None,
                "pending_entry_direction_signal": None,
                "pending_sl_price": sirusu_value,
                "asset": symbol,
            }, algo_setup)
            
            logger.info(
                f"[PAPER] FILLED: {entry_side.upper()} {lot_size} {symbol} "
                f"@ ${fill_price:.5f} | Margin: ${margin_required:.2f} | "
                f"Fee: ${entry_fee:.4f} | Liq: ${liquidation_price:.5f}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[PAPER] Exception in _execute_virtual_fill: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    # ==================== EXIT ====================
    
    async def execute_virtual_exit(
        self,
        client,
        algo_setup: Dict[str, Any],
        exit_reason: str,
        exit_price: Optional[float] = None
    ) -> Tuple[bool, float, str]:
        """Execute a virtual exit (paper trade equivalent of execute_exit)."""
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            user_id = algo_setup.get("user_id", "")
            current_position = algo_setup.get("current_position")
            entry_price = algo_setup.get("last_entry_price", 0)
            leverage = algo_setup.get("paper_leverage") or PAPER_TRADE_DEFAULT_LEVERAGE
            
            if not current_position:
                logger.warning(f"[PAPER] No position to exit for {symbol}")
                return False, 0.0, ""
            
            # Get live price for realistic slippage
            if not exit_price:
                exit_price = await get_latest_price(client, symbol)
            if not exit_price:
                logger.error(f"[PAPER] Cannot get live price for exit: {symbol}")
                return False, 0.0, ""
            
            # Calculate PnL
            pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position)
            exit_fee = exit_price * lot_size * PAPER_TRADE_TAKER_FEE
            net_pnl = pnl - exit_fee
            pnl_inr = net_pnl * settings.usd_to_inr_rate
            
            # Get margin that was locked
            sl_data = self._active_stop_losses.get(setup_id, {})
            margin_used = sl_data.get("margin_used", (entry_price * lot_size) / leverage)
            
            # Update paper balance (release margin, add PnL, deduct exit fee)
            paper_bal = await get_paper_balance(user_id)
            if paper_bal:
                new_balance = paper_bal["balance"] + net_pnl
                new_locked = max(0, paper_bal.get("locked_margin", 0) - margin_used)
                new_total_pnl = paper_bal.get("total_pnl", 0) + net_pnl
                new_total_fees = paper_bal.get("total_fees", 0) + exit_fee
                new_total_trades = paper_bal.get("total_trades", 0) + 1
                new_wins = paper_bal.get("total_wins", 0) + (1 if net_pnl > 0 else 0)
                new_losses = paper_bal.get("total_losses", 0) + (1 if net_pnl < 0 else 0)
                
                await update_paper_balance(user_id, {
                    "balance": new_balance,
                    "locked_margin": new_locked,
                    "total_pnl": new_total_pnl,
                    "total_fees": new_total_fees,
                    "total_trades": new_total_trades,
                    "total_wins": new_wins,
                    "total_losses": new_losses,
                })
            
            # Update activity record
            activity = await get_open_activity_by_setup(setup_id)
            if activity:
                await update_algo_activity(str(activity["_id"]), {
                    "exit_time": datetime.utcnow(),
                    "exit_price": exit_price,
                    "pnl": round(net_pnl, 4),
                    "pnl_inr": round(pnl_inr, 2),
                    "sirusu_exit_signal": exit_reason,
                    "is_closed": True,
                    "paper_fees": (activity.get("paper_fees", 0) or 0) + exit_fee,
                })
            
            # Clean up setup state
            await _update_setup_state(setup_id, {
                "current_position": None,
                "last_entry_price": None,
                "pending_entry_order_id": None,
                "pending_entry_side": None,
                "pending_entry_direction_signal": None,
                "entry_trigger_price": None,
                "pending_sl_price": None,
                "stop_loss_order_id": None,
            }, algo_setup)
            
            # Remove from active monitoring
            self._active_stop_losses.pop(setup_id, None)
            self._pending_entries.pop(setup_id, None)
            
            logger.info(
                f"[PAPER] EXIT: {current_position.upper()} {lot_size} {symbol} "
                f"@ ${exit_price:.5f} | PnL: ${net_pnl:.4f} | "
                f"Fee: ${exit_fee:.4f} | Reason: {exit_reason}"
            )
            return True, exit_price, exit_reason
            
        except Exception as e:
            logger.error(f"[PAPER] Exception in execute_virtual_exit: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, 0.0, ""
    
    # ==================== MONITORING ====================
    
    async def check_pending_entries(self, client) -> None:
        """Check if any pending virtual breakout entries should be filled."""
        if not self._pending_entries:
            return
        
        filled_setups = []
        
        for setup_id, entry_data in list(self._pending_entries.items()):
            try:
                symbol = entry_data["symbol"]
                side = entry_data["side"]
                trigger_price = entry_data["trigger_price"]
                
                live_price = await get_latest_price(client, symbol)
                if not live_price:
                    continue
                
                triggered = False
                if side == "long" and live_price >= trigger_price:
                    triggered = True
                elif side == "short" and live_price <= trigger_price:
                    triggered = True
                
                if triggered:
                    logger.info(
                        f"[PAPER] Breakout triggered: {side.upper()} {symbol} "
                        f"trigger=${trigger_price:.5f}, live=${live_price:.5f}"
                    )
                    
                    algo_setup = await _get_setup_by_id(setup_id)
                    if not algo_setup:
                        filled_setups.append(setup_id)
                        continue
                    
                    leverage = entry_data["leverage"]
                    margin_required = (live_price * entry_data["lot_size"]) / leverage
                    entry_fee = live_price * entry_data["lot_size"] * PAPER_TRADE_TAKER_FEE
                    
                    # Release the pre-locked margin (use stored amount, not recalculated)
                    user_id = entry_data["user_id"]
                    paper_bal = await get_paper_balance(user_id)
                    if paper_bal:
                        old_margin = entry_data.get("margin_locked", (trigger_price * entry_data["lot_size"]) / leverage)
                        new_locked = max(0, paper_bal.get("locked_margin", 0) - old_margin)
                        await update_paper_balance(user_id, {"locked_margin": new_locked})
                    
                    success = await self._execute_virtual_fill(
                        client=client,
                        algo_setup=algo_setup,
                        entry_side=side,
                        fill_price=live_price,
                        sirusu_value=entry_data["sirusu_value"],
                        leverage=leverage,
                        margin_required=margin_required,
                        entry_fee=entry_fee
                    )
                    
                    if success:
                        filled_setups.append(setup_id)
                    else:
                        # Fill failed — re-lock the margin we just released
                        paper_bal = await get_paper_balance(user_id)
                        if paper_bal:
                            re_locked = paper_bal.get("locked_margin", 0) + old_margin
                            await update_paper_balance(user_id, {"locked_margin": re_locked})
                        logger.warning(f"[PAPER] Fill failed for {symbol}, margin re-locked")
                        
            except Exception as e:
                logger.error(f"[PAPER] Error checking pending entry {setup_id}: {e}")
        
        for setup_id in filled_setups:
            self._pending_entries.pop(setup_id, None)
    
    async def check_stop_losses(self, client) -> None:
        """Check if any virtual stop-losses or liquidations should trigger."""
        if not self._active_stop_losses:
            return
        
        triggered_setups = []
        
        for setup_id, sl_data in list(self._active_stop_losses.items()):
            try:
                symbol = sl_data["symbol"]
                side = sl_data["side"]
                sl_price = sl_data["sl_price"]
                liquidation_price = sl_data["liquidation_price"]
                
                live_price = await get_latest_price(client, symbol)
                if not live_price:
                    continue
                
                exit_reason = None
                
                # Check liquidation first (more severe)
                if side == "long" and live_price <= liquidation_price:
                    exit_reason = f"LIQUIDATED (price ${live_price:.5f} <= liq ${liquidation_price:.5f})"
                elif side == "short" and live_price >= liquidation_price:
                    exit_reason = f"LIQUIDATED (price ${live_price:.5f} >= liq ${liquidation_price:.5f})"
                # Check stop-loss
                elif side == "long" and live_price <= sl_price:
                    exit_reason = f"Stop-loss hit (price ${live_price:.5f} <= SL ${sl_price:.5f})"
                elif side == "short" and live_price >= sl_price:
                    exit_reason = f"Stop-loss hit (price ${live_price:.5f} >= SL ${sl_price:.5f})"
                
                if exit_reason:
                    logger.info(f"[PAPER] {exit_reason} for {symbol}")
                    
                    algo_setup = await _get_setup_by_id(setup_id)
                    if not algo_setup:
                        triggered_setups.append(setup_id)
                        continue
                    
                    success, _, _ = await self.execute_virtual_exit(
                        client=client,
                        algo_setup=algo_setup,
                        exit_reason=exit_reason,
                        exit_price=live_price
                    )
                    
                    if success:
                        triggered_setups.append(setup_id)
                        
            except Exception as e:
                logger.error(f"[PAPER] Error checking SL {setup_id}: {e}")
        
        for setup_id in triggered_setups:
            self._active_stop_losses.pop(setup_id, None)
    
    async def update_stop_loss(self, setup_id: str, new_sl_price: float) -> None:
        """Update virtual stop-loss price (e.g., when Sirusu recalculates)."""
        if setup_id in self._active_stop_losses:
            old_sl = self._active_stop_losses[setup_id]["sl_price"]
            self._active_stop_losses[setup_id]["sl_price"] = new_sl_price
            # Persist to DB so reboot recovery uses the updated SL
            await _update_setup_state(setup_id, {"pending_sl_price": new_sl_price})
            logger.info(
                f"[PAPER] SL updated for {setup_id}: "
                f"${old_sl:.5f} -> ${new_sl_price:.5f}"
            )
    
    async def restore_open_positions(self, client) -> int:
        """Restore open paper positions and pending entries after bot restart."""
        try:
            from database.crud import get_open_paper_positions, get_all_active_algo_setups, get_all_active_screener_setups
            
            open_positions = await get_open_paper_positions()
            restored = 0
            
            # ---- Restore open positions into _active_stop_losses ----
            for pos in open_positions:
                setup_id = pos.get("algo_setup_id")
                if not setup_id or setup_id in self._active_stop_losses:
                    continue
                
                algo_setup = await _get_setup_by_id(setup_id)
                if not algo_setup:
                    continue
                
                symbol = pos.get("asset", "")
                side = pos.get("direction", "")
                entry_price = pos.get("entry_price") or 0
                lot_size = pos.get("lot_size") or 1
                leverage = pos.get("paper_leverage") or algo_setup.get("paper_leverage") or PAPER_TRADE_DEFAULT_LEVERAGE
                liquidation_price = pos.get("paper_liquidation_price") or 0
                sl_price = algo_setup.get("pending_sl_price") or 0
                margin_used = pos.get("paper_margin_used") or ((entry_price * lot_size) / leverage if leverage else 0)
                user_id = pos.get("user_id", "")
                
                # Skip if SL price is missing/zero — would cause false triggers
                if not sl_price:
                    logger.warning(
                        f"[PAPER] Restored position {symbol} has no SL price — "
                        f"will rely on liquidation price only until next Sirusu update"
                    )
                
                if not liquidation_price:
                    if side == "long":
                        liquidation_price = entry_price * (1 - 1 / leverage)
                    else:
                        liquidation_price = entry_price * (1 + 1 / leverage)
                
                self._active_stop_losses[setup_id] = {
                    "symbol": symbol,
                    "side": side,
                    "sl_price": sl_price,
                    "entry_price": entry_price,
                    "lot_size": lot_size,
                    "leverage": leverage,
                    "liquidation_price": liquidation_price,
                    "margin_used": margin_used,
                    "user_id": user_id,
                }
                
                restored += 1
                logger.info(
                    f"[PAPER] Restored position: {side.upper()} {symbol} "
                    f"@ ${entry_price:.5f} | SL: ${sl_price:.5f}"
                )
            
            # ---- Restore pending entries into _pending_entries ----
            all_algo = await get_all_active_algo_setups()
            all_screener = await get_all_active_screener_setups()
            all_setups = all_algo + all_screener
            pending_restored = 0
            for setup in all_setups:
                if not is_paper_trade(setup):
                    continue
                setup_id = str(setup["_id"])
                pending_order_id = setup.get("pending_entry_order_id")
                current_position = setup.get("current_position")
                # Only restore if has pending order and no open position
                if not pending_order_id or current_position:
                    continue
                if setup_id in self._pending_entries:
                    continue
                
                trigger_price = setup.get("entry_trigger_price", 0)
                entry_side = setup.get("pending_entry_side", "long")
                sirusu_value = setup.get("pending_sl_price", 0)
                leverage = setup.get("paper_leverage") or PAPER_TRADE_DEFAULT_LEVERAGE
                lot_size = setup.get("lot_size", 1)
                user_id = setup.get("user_id", "")
                margin_locked = (trigger_price * lot_size) / leverage if leverage else 0
                
                self._pending_entries[setup_id] = {
                    "order_id": pending_order_id,
                    "symbol": setup.get("asset", ""),
                    "side": entry_side,
                    "trigger_price": trigger_price,
                    "lot_size": lot_size,
                    "sirusu_value": sirusu_value,
                    "leverage": leverage,
                    "user_id": user_id,
                    "setup_name": setup.get("setup_name", ""),
                    "margin_locked": margin_locked,
                    "created_at": datetime.utcnow()
                }
                pending_restored += 1
                logger.info(
                    f"[PAPER] Restored pending entry: {entry_side.upper()} {setup.get('asset', '')} "
                    f"@ ${trigger_price:.5f}"
                )
            
            if restored > 0:
                logger.info(f"[PAPER] Restored {restored} open paper positions")
            if pending_restored > 0:
                logger.info(f"[PAPER] Restored {pending_restored} pending paper entries")
            
            return restored + pending_restored
            
        except Exception as e:
            logger.error(f"[PAPER] Error restoring positions: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0
    
    async def cancel_pending_entry(self, setup_id: str) -> bool:
        """Cancel a pending virtual entry order."""
        try:
            entry_data = self._pending_entries.pop(setup_id, None)
            if entry_data:
                # Release locked margin (use stored amount for consistency)
                user_id = entry_data["user_id"]
                leverage = entry_data["leverage"]
                margin = entry_data.get("margin_locked", (entry_data["trigger_price"] * entry_data["lot_size"]) / leverage)
                
                paper_bal = await get_paper_balance(user_id)
                if paper_bal:
                    new_locked = max(0, paper_bal.get("locked_margin", 0) - margin)
                    await update_paper_balance(user_id, {"locked_margin": new_locked})
                
                # Clean up setup
                await _update_setup_state(setup_id, {
                    "pending_entry_order_id": None,
                    "entry_trigger_price": None,
                    "pending_entry_side": None,
                    "pending_entry_direction_signal": None,
                })
                
                logger.info(f"[PAPER] Cancelled pending entry for {setup_id}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"[PAPER] Error cancelling entry: {e}")
            return False
    
    # ==================== HELPERS ====================
    
    def _calculate_pnl(self, entry_price: float, exit_price: float,
                       lot_size: int, position_side: str) -> float:
        """Calculate raw PnL (before fees)."""
        try:
            ep = float(entry_price) if entry_price else 0.0
            xp = float(exit_price) if exit_price else 0.0
            if ep == 0.0 or xp == 0.0:
                return 0.0
            if position_side == "long":
                return (xp - ep) * lot_size
            else:
                return (ep - xp) * lot_size
        except (TypeError, ValueError):
            return 0.0
    
    def get_active_positions_count(self) -> int:
        """Get count of active virtual positions."""
        return len(self._active_stop_losses)
    
    def get_pending_entries_count(self) -> int:
        """Get count of pending virtual entries."""
        return len(self._pending_entries)


# Global singleton instance
paper_trader = PaperTrader()
