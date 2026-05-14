"""Trade Journal Service - Background processing for pristine ledger accounting."""
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any

from database.journal import journal_ops
from utils.accounting import pnl_engine
from utils.order_polling import get_exact_fill_details
from config.settings import settings

logger = logging.getLogger(__name__)

class JournalService:
    async def record_entry(self, client, trade_data: Dict[str, Any], order_response: Dict[str, Any]):
        """Background hook to accurately record a new trade entry."""
        try:
            trade_id = str(trade_data.get("_id") or trade_data.get("trade_id"))
            order_id = str(order_response.get("id"))
            product_id = trade_data.get("product_id")
            is_paper = trade_data.get("is_paper_trade", False)
            
            if is_paper:
                # Paper trade — use simulated prices directly (no exchange API)
                entry_price = float(order_response.get("average_fill_price", 0))
                entry_fee = float(order_response.get("fee", 0))
            else:
                # Live trade — poll Delta Exchange for exact fill
                fill_data = await get_exact_fill_details(client, order_id, product_id)
                
                if fill_data and fill_data.get("state") in ["closed", "filled"]:
                    entry_price = fill_data["fill_price"]
                    entry_fee = fill_data["fee"]
                else:
                    # Fallback to immediate response or trigger price
                    entry_price = float(order_response.get("average_fill_price") or trade_data.get("entry_trigger_price", 0))
                    entry_fee = None  # Will be calculated theoretically later
            
            # Build pristine ledger record
            journal_entry = {
                "trade_id": trade_id,
                "user_id": trade_data.get("user_id"),
                "api_name": "PaperTrade" if is_paper else (trade_data.get("api_name") or "DeltaExchange"),
                "strategy_name": trade_data.get("setup_name"),
                "asset": trade_data.get("asset"),
                "direction": trade_data.get("direction", trade_data.get("current_position")),
                "quantity": trade_data.get("lot_size"),
                "status": "open",
                "is_paper_trade": is_paper,
                
                "entry_price": entry_price,
                "entry_time": trade_data.get("entry_time") or datetime.utcnow(),
                "entry_order_id": order_id,
                "entry_fee": entry_fee,
                
                "scaling_events": []
            }
            
            await journal_ops.log_trade_event(trade_id, journal_entry)
            
        except Exception as e:
            logger.error(f"Journal record_entry failed: {e}")

    async def record_exit(self, client, trade_data: Dict[str, Any], exit_order_response: Dict[str, Any], exit_reason: str):
        """Background hook to accurately record a trade exit and calculate Net PnL."""
        try:
            trade_id = str(trade_data.get("_id") or trade_data.get("trade_id"))
            product_id = trade_data.get("product_id")
            asset = trade_data.get("asset")
            direction = trade_data.get("direction", trade_data.get("current_position"))
            quantity = trade_data.get("lot_size")
            entry_price = trade_data.get("entry_price", 0)
            is_paper = trade_data.get("is_paper_trade", False)
            
            # 1. Resolve exit fill
            exit_price = 0.0
            exit_fee = None
            exit_order_id = str(exit_order_response.get("id")) if exit_order_response else None
            
            if is_paper:
                # Paper trade — use simulated prices directly (no exchange API)
                exit_price = float(exit_order_response.get("average_fill_price", 0))
                exit_fee = float(exit_order_response.get("fee", 0))
            elif exit_order_id:
                # Live trade — poll Delta Exchange for exact fill
                fill_data = await get_exact_fill_details(client, exit_order_id, product_id)
                if fill_data and fill_data.get("state") in ["closed", "filled"]:
                    exit_price = fill_data["fill_price"]
                    exit_fee = fill_data["fee"]
                else:
                    exit_price = float(exit_order_response.get("average_fill_price", 0))
            else:
                # No exit order response (e.g. external liquidation)
                exit_price = trade_data.get("exit_price", 0)

            # 2. Calculate PnL accurately
            # For paper trades, use actual simulated fees; for live, fallback to theoretical if missing
            actual_entry_fee = None
            if is_paper:
                # Try to get stored entry fee from the journal ledger
                existing = await journal_ops.get_trade_by_id(trade_id)
                if existing:
                    actual_entry_fee = existing.get("entry_fee")
            
            gross_pnl, total_fees, net_pnl = pnl_engine.calculate_trade_pnl(
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                asset=asset,
                direction=direction,
                actual_entry_fee=actual_entry_fee,
                actual_exit_fee=exit_fee
            )
            
            # 3. Update ledger
            exit_update = {
                "status": "closed",
                "exit_price": exit_price,
                "exit_time": trade_data.get("exit_time") or datetime.utcnow(),
                "exit_reason": exit_reason,
                "exit_order_id": exit_order_id,
                "exit_fee": exit_fee,
                "gross_pnl": gross_pnl,
                "total_fees": total_fees,
                "net_pnl": net_pnl,
                "net_pnl_inr": net_pnl * settings.usd_to_inr_rate,
                "is_paper_trade": is_paper
            }
            
            await journal_ops.log_trade_event(trade_id, exit_update)
            
        except Exception as e:
            logger.error(f"Journal record_exit failed: {e}")

journal_service = JournalService()
