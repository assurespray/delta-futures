import logging
import time
import asyncio
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
from utils.market_utils import get_contract_multiplier, clamp_leverage
from services.logger_bot import logger_bot

logger = logging.getLogger(__name__)

def to_ist_str(dt: datetime) -> str:
    if not dt: return "N/A"
    from datetime import timedelta
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime('%Y-%m-%d %H:%M:%S IST')


def is_paper_trade(setup: Dict[str, Any]) -> bool:
    if ENABLE_DEMO_MODE:
        return True
    return setup.get("is_paper_trade", False)


class PaperTrader:
    """Virtual exchange engine for paper trading using TradeState."""
    
    def __init__(self):
        pass
    
    async def place_virtual_entry(
        self, client, algo_setup, entry_side, breakout_price, stop_loss_price, immediate=False
    ) -> bool:
        try:
            setup_id = str(algo_setup["_id"])
            setup_name = algo_setup["setup_name"]
            symbol = algo_setup["asset"]
            lot_size = algo_setup["lot_size"]
            user_id = algo_setup.get("user_id", "")
            raw_leverage = algo_setup.get("paper_leverage")
            if raw_leverage is None:
                raw_leverage = PAPER_TRADE_DEFAULT_LEVERAGE
            leverage = clamp_leverage(symbol, raw_leverage)
            setup_type = "screener" if "asset_selection_type" in algo_setup else "algo"
            
            live_price = await get_latest_price(client, symbol)
            if not live_price: return False
            
            multiplier = get_contract_multiplier(symbol)
            notional = live_price * lot_size * multiplier
            margin_required = notional / leverage
            entry_fee = notional * PAPER_TRADE_TAKER_FEE
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
                "pending_sl_price": stop_loss_price,
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
                    "direction": entry_side,
                    "paper_liquidation_price": liquidation_price,
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "entry_signal": "uptrend" if entry_side == "long" else "downtrend"
                })
                
                trade_id = await create_trade_state(trade_data)
                trade_data["trade_id"] = trade_id
                
                # Journal hook — record paper entry
                try:
                    from services.journal_service import journal_service
                    mock_resp = {"id": "paper_entry", "average_fill_price": live_price, "fee": entry_fee}
                    asyncio.create_task(journal_service.record_entry(trade_data, mock_resp))
                except Exception as e:
                    logger.warning(f"Journal record_entry (paper immediate) skipped: {e}")
                
                # Send [PAPER] Entry Notification
                try:
                    emoji = "🟢" if entry_side == "long" else "🔴"
                    msg = (
                        f"{emoji} **[PAPER] TRADE ENTRY**\n\n"
                        f"**Setup:** {setup_name}\n"
                        f"**Asset:** {symbol} @ {algo_setup.get('timeframe', '1m')}\n"
                        f"**Direction:** {entry_side.upper()}\n"
                        f"**Type:** MARKET\n"
                        f"**Entry Price:** ${float(live_price):.5f}\n"
                        f"**Lot Size:** {lot_size}\n"
                    )
                    if stop_loss_price:
                        msg += f"**Stop Loss:** ${float(stop_loss_price):.5f}\n"
                    msg += f"\n_Time: {to_ist_str(datetime.utcnow())}_"
                    
                    asyncio.create_task(logger_bot.send_message(msg))
                    if user_id:
                        asyncio.create_task(logger_bot.send_to_user(user_id, msg))
                except Exception as e:
                    logger.error(f"[PAPER] Notification error: {e}")
                
                return True
            else:
                new_locked = paper_bal.get("locked_margin", 0) + margin_required
                await update_paper_balance(user_id, {"locked_margin": new_locked})
                
                trade_id = await create_trade_state(trade_data)
                trade_data["trade_id"] = trade_id
                
                # Send [PAPER] Pending Order Notification
                try:
                    emoji = "🟢" if entry_side == "long" else "🔴"
                    msg = (
                        f"⏳ **[PAPER] PENDING ENTRY**\n\n"
                        f"**Setup:** {setup_name}\n"
                        f"**Asset:** {symbol} @ {algo_setup.get('timeframe', '1m')}\n"
                        f"**Direction:** {entry_side.upper()}\n"
                        f"**Type:** STOP-LIMIT\n"
                        f"**Trigger Price:** ${float(breakout_price):.5f}\n"
                        f"**Lot Size:** {lot_size}\n"
                    )
                    if stop_loss_price:
                        msg += f"**Stop Loss:** ${float(stop_loss_price):.5f}\n"
                    msg += f"\n_Time: {to_ist_str(datetime.utcnow())}_"
                    
                    asyncio.create_task(logger_bot.send_message(msg))
                    if user_id:
                        asyncio.create_task(logger_bot.send_to_user(user_id, msg))
                except Exception as e:
                    logger.error(f"[PAPER] Pending Notification error: {e}")
                
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
            current_position = trade_state.get("direction") or trade_state.get("current_position")
            entry_price = trade_state["entry_price"]
            
            if not exit_price:
                exit_price = await get_latest_price(client, symbol)
                if not exit_price:
                    return False, 0.0, ""
                    
            pnl = self._calculate_pnl(entry_price, exit_price, lot_size, current_position, symbol=symbol)
            multiplier = get_contract_multiplier(symbol)
            exit_fee = exit_price * lot_size * multiplier * PAPER_TRADE_TAKER_FEE
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
                "exit_signal": exit_reason,
                "paper_fees": (trade_state.get("paper_fees", 0) or 0) + exit_fee
            })
            
            # Close position records (prevents orphan "open" records in positions collection)
            setup_id = trade_state.get("setup_id")
            if setup_id:
                try:
                    db = await get_db()
                    await db.positions.update_many(
                        {"algo_setup_id": setup_id, "status": "open"},
                        {"$set": {"closed_at": datetime.utcnow(), "status": "closed"}}
                    )
                except Exception as e:
                    logger.error(f"[PAPER] Error closing position records for {symbol}: {e}")
            
            # Journal hook — record paper exit
            try:
                from services.journal_service import journal_service
                mock_exit_resp = {"id": "paper_exit", "average_fill_price": exit_price, "fee": exit_fee}
                asyncio.create_task(journal_service.record_exit(trade_state, mock_exit_resp, exit_reason))
            except Exception as e:
                logger.warning(f"Journal record_exit (paper) skipped: {e}")
                
            # Send [PAPER] Exit Notification
            try:
                pnl_emoji = "💰" if net_pnl >= 0 else "📉"
                msg = (
                    f"🚪 **[PAPER] TRADE EXIT**\n\n"
                    f"**Setup:** {trade_state.get('setup_name', 'Unknown')}\n"
                    f"**Asset:** {symbol} @ {trade_state.get('timeframe', '1m')}\n"
                    f"**Direction:** {current_position.upper()}\n"
                    f"**Exit Reason:** {exit_reason}\n\n"
                    f"**Entry Price:** ${float(entry_price):.5f}\n"
                    f"**Exit Price:** ${float(exit_price):.5f}\n"
                    f"**Lot Size:** {lot_size}\n"
                    f"**{pnl_emoji} Net PnL:** ${net_pnl:.2f} (₹{pnl_inr:.2f})\n"
                    f"\n_Time: {to_ist_str(datetime.utcnow())}_"
                )
                asyncio.create_task(logger_bot.send_message(msg))
                if user_id:
                    asyncio.create_task(logger_bot.send_to_user(user_id, msg))
            except Exception as e:
                logger.error(f"[PAPER] Exit Notification error: {e}")
            
            return True, exit_price, exit_reason
        except Exception as e:
            logger.error(f"[PAPER] Error in execute_virtual_exit: {e}")
            return False, 0.0, ""

    async def check_pending_entries(self, client) -> None:
        pending_trades = await get_pending_entry_trade_states()
        
        for trade in pending_trades:
            if not trade.get("is_paper_trade"): continue
                
            symbol = trade.get("asset")
            if not symbol: continue
            
            live_price = await get_latest_price(client, symbol)
            if not live_price: continue
                
            side = trade.get("pending_entry_side")
            trigger = trade.get("entry_trigger_price")
            if not side or not trigger: continue
            
            if (side == "long" and live_price >= trigger) or (side == "short" and live_price <= trigger):
                raw_leverage = trade.get("paper_leverage", PAPER_TRADE_DEFAULT_LEVERAGE)
                leverage = clamp_leverage(symbol, raw_leverage)
                lot_size = trade.get("lot_size", 1)
                multiplier = get_contract_multiplier(symbol)
                entry_fee = live_price * lot_size * multiplier * PAPER_TRADE_TAKER_FEE
                liquidation_price = live_price * (1 - 1/leverage) if side == "long" else live_price * (1 + 1/leverage)
                
                user_id = trade.get("user_id", "")
                paper_bal = await get_paper_balance(user_id)
                if paper_bal:
                    new_balance = paper_bal["balance"] - entry_fee
                    await update_paper_balance(user_id, {"balance": new_balance})
                    
                await update_trade_state(str(trade["_id"]), {
                    "status": "open",
                    "entry_price": live_price,
                    "entry_time": datetime.utcnow(),
                    "direction": side,
                    "paper_liquidation_price": liquidation_price,
                    "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "entry_signal": "uptrend" if side == "long" else "downtrend",
                    "paper_fees": (trade.get("paper_fees", 0) or 0) + entry_fee
                })
                
                # Journal hook — record paper pending entry fill
                try:
                    from services.journal_service import journal_service
                    trade["entry_price"] = live_price
                    trade["entry_time"] = datetime.utcnow()
                    mock_resp = {"id": "paper_pending_fill", "average_fill_price": live_price, "fee": entry_fee}
                    asyncio.create_task(journal_service.record_entry(trade, mock_resp))
                except Exception as e:
                    logger.warning(f"Journal record_entry (paper pending) skipped: {e}")
                    
                # Send [PAPER] Entry Fill Notification
                try:
                    setup_name = trade.get("setup_name", "Unknown")
                    emoji = "🟢" if side == "long" else "🔴"
                    msg = (
                        f"{emoji} **[PAPER] TRADE ENTRY**\n\n"
                        f"**Setup:** {setup_name}\n"
                        f"**Asset:** {symbol} @ {trade.get('timeframe', '1m')}\n"
                        f"**Direction:** {side.upper()}\n"
                        f"**Type:** PENDING ORDER FILLED\n"
                        f"**Entry Price:** ${float(live_price):.5f}\n"
                        f"**Lot Size:** {lot_size}\n"
                    )
                    sl_price = trade.get("pending_sl_price")
                    if sl_price:
                        msg += f"**Stop Loss:** ${float(sl_price):.5f}\n"
                    msg += f"\n_Time: {to_ist_str(datetime.utcnow())}_"
                    
                    asyncio.create_task(logger_bot.send_message(msg))
                    if user_id:
                        asyncio.create_task(logger_bot.send_to_user(user_id, msg))
                except Exception as e:
                    logger.error(f"[PAPER] Pending Fill Notification error: {e}")

    async def check_stop_losses(self, client) -> None:
        open_trades = await get_open_trade_states()
        
        for trade in open_trades:
            if not trade.get("is_paper_trade"): continue
                
            symbol = trade.get("asset")
            if not symbol: continue
            
            live_price = await get_latest_price(client, symbol)
            if not live_price: continue
                
            side = trade.get("direction") or trade.get("current_position")
            if not side: continue
            
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
            
            # Send [PAPER] Order Cancelled Notification
            try:
                setup_name = trade.get("setup_name", "Unknown")
                symbol = trade.get("asset", "Unknown")
                side = trade.get("pending_entry_side", "").upper()
                msg = (
                    f"⚠️ **[PAPER] PENDING ORDER CANCELLED**\n\n"
                    f"**Setup:** {setup_name}\n"
                    f"**Asset:** {symbol} @ {trade.get('timeframe', '1m')}\n"
                    f"**Direction:** {side}\n"
                    f"**Reason:** Setup invalidated (e.g. price reversed before entry hit)\n"
                    f"\n_Time: {to_ist_str(datetime.utcnow())}_"
                )
                asyncio.create_task(logger_bot.send_message(msg))
                if user_id:
                    asyncio.create_task(logger_bot.send_to_user(user_id, msg))
            except Exception as e:
                logger.error(f"[PAPER] Cancel Notification error: {e}")
                
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

    def _calculate_pnl(self, ep, xp, ls, side, symbol=""):
        ep, xp = float(ep or 0), float(xp or 0)
        if ep == 0 or xp == 0: return 0.0
        from utils.market_utils import get_contract_multiplier
        multiplier = get_contract_multiplier(symbol)
        return (xp - ep) * ls * multiplier if side == "long" else (ep - xp) * ls * multiplier

paper_trader = PaperTrader()
