import logging
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from api.market_data import get_latest_price, get_product_by_symbol
from database.crud import (
    create_trade_state, update_trade_state, get_trade_state_by_id,
    get_open_trade_states, get_pending_entry_trade_states,
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


def is_paper_trade(setup: Dict[str, Any]) -> bool:
    if ENABLE_DEMO_MODE:
        return True
    return setup.get("is_paper_trade", False)


class PaperTrader:
    """Virtual exchange engine for paper trading using TradeState."""
    
    def __init__(self):
        pass
    
    async def place_virtual_entry(
        self, client, algo_setup, entry_side, breakout_price, sirusu_value, immediate=False
    ) -> bool:
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            user_id = algo_setup.get("user_id", "")
            leverage = algo_setup.get("paper_leverage") or PAPER_TRADE_DEFAULT_LEVERAGE
            setup_type = "screener" if "asset_selection_type" in algo_setup else "algo"
            
            live_price = await get_latest_price(client, symbol)
            if not live_price: return False
            
            margin_required = (live_price * lot_size) / leverage
            entry_fee = live_price * lot_size * PAPER_TRADE_TAKER_FEE
            total_cost = margin_required + entry_fee
            
            paper_bal = await get_paper_balance(user_id)
            if not paper_bal or total_cost > (paper_bal["balance"] - paper_bal.get("locked_margin", 0)):
                return False
                
            trade_data = {
                "user_id": user_id,
                "setup_id": setup_id,
                "setup_type": setup_type,
                "setup_name": setup_name,
                "asset": symbol,
                "direction": entry_side,
                "lot_size": lot_size,
                "timeframe": algo_setup.get("timeframe", "1m"),
                "status": "pending_entry",
                "entry_trigger_price": breakout_price,
                "pending_entry_side": entry_side,
                "pending_sl_price": sirusu_value,
                "is_paper_trade": True,
                "paper_leverage": leverage,
                "paper_margin_used": margin_required,
                "paper_fees": entry_fee
            }

            if immediate:
                liquidation_price = live_price * (1 - 1/leverage) if entry_side == "long" else live_price * (1 + 1/leverage)
                
                new_balance = paper_bal["balance"] - entry_fee
                new_locked = paper_bal.get("locked_margin", 0) + margin_required
                await update_paper_balance(user_id, {"balance": new_balance, "locked_margin": new_locked})
                
                trade_data.update({
                    "status": "open",
                    "entry_price": live_price,
                    "entry_time": datetime.utcnow(),
                    "current_position": entry_side,
                    "paper_liquidation_price": liquidation_price,
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "perusu_entry_signal": "uptrend" if entry_side == "long" else "downtrend"
                })
                
                await create_trade_state(trade_data)
                return True
            else:
                new_locked = paper_bal.get("locked_margin", 0) + margin_required
                await update_paper_balance(user_id, {"locked_margin": new_locked})
                
                await create_trade_state(trade_data)
                return True
                
        except Exception as e:
            logger.error(f"[PAPER] Error in place_virtual_entry: {e}")
            return False

    async def execute_virtual_exit(self, client, trade_state, exit_reason: str, exit_price=None) -> tuple[bool, float, str]:
        try:
            trade_id = str(trade_state["_id"])
            symbol = trade_state["asset"]
            lot_size = trade_state["lot_size"]
            user_id = trade_state["user_id"]
            current_position = trade_state["current_position"]
            entry_price = trade_state["entry_price"]
            
            if not exit_price:
                exit_price = await get_latest_price(client, symbol)
                if not exit_price:
                    return False, 0.0, ""
                    
            pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position)
            exit_fee = exit_price * lot_size * PAPER_TRADE_TAKER_FEE
            net_pnl = pnl - exit_fee
            pnl_inr = net_pnl * settings.usd_to_inr_rate
            
            margin_used = trade_state.get("paper_margin_used", 0)
            
            paper_bal = await get_paper_balance(user_id)
            if paper_bal:
                new_balance = paper_bal["balance"] + net_pnl
                new_locked = max(0, paper_bal.get("locked_margin", 0) - margin_used)
                await update_paper_balance(user_id, {"balance": new_balance, "locked_margin": new_locked})
                
            await update_trade_state(trade_id, {
                "status": "closed",
                "exit_price": exit_price,
                "exit_time": datetime.utcnow(),
                "pnl": net_pnl,
                "pnl_inr": pnl_inr,
                "sirusu_exit_signal": exit_reason,
                "paper_fees": (trade_state.get("paper_fees", 0) or 0) + exit_fee
            })
            
            return True, exit_price, exit_reason
        except Exception as e:
            logger.error(f"[PAPER] Error in execute_virtual_exit: {e}")
            return False, 0.0, ""

    async def check_pending_entries(self, client) -> None:
        pending_trades = await get_pending_entry_trade_states()
        
        for trade in pending_trades:
            if not trade.get("is_paper_trade"): continue
                
            symbol = trade["asset"]
            live_price = await get_latest_price(client, symbol)
            if not live_price: continue
                
            side = trade["pending_entry_side"]
            trigger = trade["entry_trigger_price"]
            
            if (side == "long" and live_price >= trigger) or (side == "short" and live_price <= trigger):
                leverage = trade["paper_leverage"]
                entry_fee = live_price * trade["lot_size"] * PAPER_TRADE_TAKER_FEE
                liquidation_price = live_price * (1 - 1/leverage) if side == "long" else live_price * (1 + 1/leverage)
                
                user_id = trade["user_id"]
                paper_bal = await get_paper_balance(user_id)
                if paper_bal:
                    new_balance = paper_bal["balance"] - entry_fee
                    await update_paper_balance(user_id, {"balance": new_balance})
                    
                await update_trade_state(str(trade["_id"]), {
                    "status": "open",
                    "entry_price": live_price,
                    "entry_time": datetime.utcnow(),
                    "current_position": side,
                    "paper_liquidation_price": liquidation_price,
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "perusu_entry_signal": "uptrend" if side == "long" else "downtrend",
                    "paper_fees": (trade.get("paper_fees", 0) or 0) + entry_fee
                })

    async def check_stop_losses(self, client) -> None:
        open_trades = await get_open_trade_states()
        
        for trade in open_trades:
            if not trade.get("is_paper_trade"): continue
                
            symbol = trade["asset"]
            live_price = await get_latest_price(client, symbol)
            if not live_price: continue
                
            side = trade["current_position"]
            sl_price = trade.get("pending_sl_price", 0)
            liq_price = trade.get("paper_liquidation_price", 0)
            
            exit_reason = None
            if side == "long":
                if liq_price and live_price <= liq_price:
                    exit_reason = f"LIQUIDATED (price <= {liq_price})"
                elif sl_price and live_price <= sl_price:
                    exit_reason = f"Stop-loss hit (price <= {sl_price})"
            else:
                if liq_price and live_price >= liq_price:
                    exit_reason = f"LIQUIDATED (price >= {liq_price})"
                elif sl_price and live_price >= sl_price:
                    exit_reason = f"Stop-loss hit (price >= {sl_price})"
                    
            if exit_reason:
                await self.execute_virtual_exit(client, trade, exit_reason, live_price)

    async def update_stop_loss(self, trade_id: str, new_sl_price: float) -> None:
        await update_trade_state(trade_id, {"pending_sl_price": new_sl_price})

    async def restore_open_positions(self, client) -> int:
        return 0

    async def cancel_pending_entry(self, trade_id: str) -> bool:
        trade = await get_trade_state_by_id(trade_id)
        if trade and trade["status"] == "pending_entry":
            user_id = trade["user_id"]
            margin = trade.get("paper_margin_used", 0)
            
            paper_bal = await get_paper_balance(user_id)
            if paper_bal:
                new_locked = max(0, paper_bal.get("locked_margin", 0) - margin)
                await update_paper_balance(user_id, {"locked_margin": new_locked})
                
            await update_trade_state(trade_id, {"status": "cancelled"})
            return True
        return False

    async def force_cleanup_setup(self, setup_id: str) -> None:
        db = await get_db()
        cursor = db.trade_states.find({"setup_id": setup_id, "status": {"$in": ["open", "pending_entry"]}})
        trades = await cursor.to_list(100)
        for trade in trades:
            user_id = trade["user_id"]
            margin = trade.get("paper_margin_used", 0)
            paper_bal = await get_paper_balance(user_id)
            if paper_bal:
                new_locked = max(0, paper_bal.get("locked_margin", 0) - margin)
                await update_paper_balance(user_id, {"locked_margin": new_locked})
            await update_trade_state(str(trade["_id"]), {"status": "cancelled"})

    def get_active_positions_count(self) -> int: return 1
    def get_pending_entries_count(self) -> int: return 1

    def _calculate_pnl(self, ep, xp, ls, side):
        ep, xp = float(ep or 0), float(xp or 0)
        if ep == 0 or xp == 0: return 0.0
        return (xp - ep) * ls if side == "long" else (ep - xp) * ls

paper_trader = PaperTrader()
