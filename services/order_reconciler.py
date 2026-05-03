import logging
from datetime import datetime
from database.crud import (
    get_db, release_position_lock
)
from api.delta_client import DeltaExchangeClient
from strategy.position_manager import PositionManager

logger = logging.getLogger(__name__)


async def reconcile_pending_orders(logger_bot=None):
    from database.crud import get_open_trade_states, get_pending_entry_trade_states, update_trade_state, get_algo_setup_by_id, get_screener_setup_by_id, get_api_credential_by_id
    from api.delta_client import DeltaExchangeClient
    from api.orders import get_order_status_by_id
    
    open_trades = await get_open_trade_states()
    pending_trades = await get_pending_entry_trade_states()
    
    for trade in open_trades + pending_trades:
        if trade.get("is_paper_trade"):
            continue
            
        trade_id = str(trade["_id"])
        setup_id = trade["setup_id"]
        setup = await get_algo_setup_by_id(setup_id) or await get_screener_setup_by_id(setup_id)
        if not setup:
            # Orphan trade: parent setup was deleted but trade wasn't cleaned up
            logger.warning(f"[RECON] Orphan trade found: {trade.get('asset')} (setup_id={setup_id} no longer exists). Force-closing.")
            entry_price = trade.get("entry_price") or trade.get("last_entry_price") or trade.get("entry_trigger_price") or 0
            await update_trade_state(trade_id, {
                "status": "closed",
                "exit_price": entry_price,
                "exit_time": datetime.utcnow(),
                "pnl": 0.0,
                "pnl_inr": 0.0,
                "exit_signal": "Setup deleted (orphan trade closed)"
            })
            try:
                from database.crud import get_db, release_position_lock
                db = await get_db()
                await release_position_lock(db, trade.get("asset", ""), setup_id)
            except Exception:
                pass
            continue
        
        api_id = setup.get("api_id")
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred: continue
        
        client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
        symbol = trade["asset"]
        product_id = trade.get("product_id")
        
        try:
            if trade["status"] == "open":
                # Backfill: repair missing entry_price from legacy fields
                if not trade.get("entry_price"):
                    backfill_price = trade.get("last_entry_price") or trade.get("entry_trigger_price")
                    if backfill_price:
                        await update_trade_state(trade_id, {"entry_price": backfill_price})
                        trade["entry_price"] = backfill_price
                        logger.info(f"[RECON] Backfilled entry_price=${backfill_price} for {symbol}")
                
                # Check 1: SL order filled?
                sl_order_id = trade.get("stop_loss_order_id")
                if sl_order_id and product_id:
                    status = await get_order_status_by_id(client, sl_order_id, product_id)
                    if status == "filled":
                        from strategy.position_manager import PositionManager
                        pm = PositionManager()
                        # The SL was hit!
                        await pm.execute_exit(client, trade, "Stop Loss Triggered")
                        continue
                
                # Check 2: Position closed externally (manual close, liquidation, etc.)?
                from api.positions import get_position_by_symbol
                actual_position = await get_position_by_symbol(client, symbol)
                actual_size = actual_position.get("size", 0) if actual_position else 0
                
                # Detect direction flip: exchange has opposite position to what bot expects
                current_position = trade.get("current_position") or trade.get("direction")
                actual_direction = "long" if actual_size > 0 else "short" if actual_size < 0 else None
                position_flipped = actual_size != 0 and actual_direction != current_position
                
                if actual_size == 0 or position_flipped:
                    if position_flipped:
                        logger.warning(
                            f"[RECON] Direction mismatch for {symbol}: bot expects {current_position.upper()} "
                            f"but exchange has {actual_direction.upper()} (size={actual_size}). Syncing..."
                        )
                    else:
                        logger.info(f"[RECON] Position {symbol} is closed on exchange but DB says open. Syncing...")
                    from strategy.position_manager import PositionManager
                    pm = PositionManager()
                    exit_reason = "Position direction flipped externally" if position_flipped else "Position closed externally"
                    success, exit_price, _ = await pm.execute_exit(client, trade, exit_reason)
                    
                    # Safety net: if execute_exit failed (e.g. missing fields), force-close in DB
                    if not success:
                        logger.warning(f"[RECON] execute_exit failed for ghost {symbol}. Force-closing in DB.")
                        entry_price = trade.get("entry_price") or trade.get("last_entry_price") or trade.get("entry_trigger_price") or 0
                        await update_trade_state(trade_id, {
                            "status": "closed",
                            "exit_price": entry_price,
                            "exit_time": datetime.utcnow(),
                            "pnl": 0.0,
                            "pnl_inr": 0.0,
                            "exit_signal": "Position closed externally (force-synced)"
                        })
                        
                        try:
                            import asyncio
                            from services.journal_service import journal_service
                            trade["trade_id"] = trade_id
                            trade["exit_price"] = entry_price
                            trade["exit_time"] = datetime.utcnow()
                            asyncio.create_task(journal_service.record_exit(client, trade, None, "Position closed externally (force-synced)"))
                        except Exception as e:
                            pass
                            
                        from database.crud import get_db, release_position_lock
                        db = await get_db()
                        await release_position_lock(db, symbol, trade["setup_id"])
                    
                    # Telegram notification for external close
                    if logger_bot:
                        try:
                            setup_name = trade.get("setup_name", setup.get("setup_name", "Unknown"))
                            entry_price = trade.get("entry_price", 0)
                            exit_price_display = f"${exit_price:.2f}" if exit_price else "unknown"
                            await logger_bot.send_warning(
                                f"⚠️ [RECON] Position closed externally!\n\n"
                                f"Setup: {setup_name}\n"
                                f"Asset: {symbol}\n"
                                f"Direction: {current_position.upper() if current_position else 'N/A'}\n"
                                f"Entry: ${entry_price}\n"
                                f"Exit: {exit_price_display}\n"
                                f"Reason: {exit_reason}"
                            )
                        except Exception as e:
                            logger.error(f"[RECON] Error sending external close notification: {e}")
            
            elif trade["status"] == "pending_entry":
                order_id = trade.get("pending_entry_order_id")
                if order_id and product_id:
                    status = await get_order_status_by_id(client, order_id, product_id)
                    if status in ("cancelled", "rejected", "closed"):
                        await update_trade_state(trade_id, {"status": "cancelled", "pending_entry_order_id": None})
                        from database.crud import get_db, release_position_lock
                        db = await get_db()
                        await release_position_lock(db, symbol, trade["setup_id"])
                    elif status == "filled":
                        from strategy.position_manager import PositionManager
                        pm = PositionManager()
                        await pm.check_entry_order_filled(client, trade, None, logger_bot=logger_bot)
                elif not order_id:
                    # Stale pending trade with no order ID — can never fill, cancel it
                    logger.warning(f"[RECON] Stale pending trade for {symbol} has no order_id. Cancelling.")
                    await update_trade_state(trade_id, {"status": "cancelled", "pending_entry_order_id": None})
                    from database.crud import get_db, release_position_lock
                    db = await get_db()
                    await release_position_lock(db, symbol, trade["setup_id"])
        except Exception as e:
            logger.error(f"[RECON] Error processing trade {trade_id} ({symbol}): {e}")
        finally:
            await client.close()
