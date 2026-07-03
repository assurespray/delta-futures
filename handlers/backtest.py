"""
Backtest Orchestrator & Live Progress UI

Coordinates the fetcher, engine, and analytics in an asynchronous background task.
Provides a live-updating Telegram progress bar to prevent webhook timeouts.
"""

import os
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from telegram import Update, Message
from telegram.ext import ContextTypes

from api.delta_client import DeltaExchangeClient
from services.backtest_fetcher import BacktestFetcher, estimate_download_time_seconds
from services.backtest_engine import BacktestEngine
from utils.backtest_metrics import calculate_metrics
from utils.monte_carlo import run_advanced_analytics
from utils.backtest_exporter import generate_equity_curve_chart, generate_trade_log_csv
from database.crud import save_backtest_result, get_api_credential_by_id

logger = logging.getLogger(__name__)

# How often to update the Telegram message (seconds)
# Telegram limits edits to ~1 per second. 3-4s is safe.
UI_UPDATE_INTERVAL = 3.5 

def generate_progress_bar(current: int, total: int, width: int = 15) -> str:
    """Generate a text-based progress bar [████░░░░]"""
    if total <= 0:
        return f"[{'░' * width}] 0%"
    
    pct = min(1.0, max(0.0, current / total))
    filled = int(width * pct)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {int(pct * 100)}%"

async def run_backtest_task(
    chat_id: int,
    message_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    api_id: str,
    symbol: str,
    timeframe: str,
    days: Optional[int],
    strategy_params: Dict[str, Any],
    custom_start_ts: Optional[int] = None,
    custom_end_ts: Optional[int] = None
):
    """
    The main asynchronous task that runs the backtest.
    It edits the original Telegram message to show progress, 
    and sends the final results when finished.
    """
    start_cpu_time = time.time()
    last_ui_update = time.time()
    
    # Track UI state
    ui_state = {
        "status": "Initializing...",
        "current": 0,
        "total": 100,
        "eta": "Calculating..."
    }

    async def _update_ui(current: int, total: int, status_msg: str, force: bool = False):
        """Callback to update the Telegram message."""
        nonlocal last_ui_update
        now = time.time()
        
        ui_state["current"] = current
        ui_state["total"] = total
        ui_state["status"] = status_msg
        
        # Calculate crude ETA
        elapsed = now - start_cpu_time
        if current > 0 and total > 0:
            rate = current / elapsed
            remaining = total - current
            if rate > 0:
                eta_secs = remaining / rate
                ui_state["eta"] = f"{int(eta_secs)} seconds"
        
        if force or (now - last_ui_update >= UI_UPDATE_INTERVAL):
            bar = generate_progress_bar(current, total)
            text = (
                f"🧪 **Backtest in Progress**\n\n"
                f"**Asset:** {symbol} | **TF:** {timeframe} | **Days:** {days}\n"
                f"**Status:** {status_msg}\n\n"
                f"**Progress:** {bar}\n"
                f"**ETA:** {ui_state['eta']}\n\n"
                f"⏳ _Please wait, doing heavy math in background..._"
            )
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="Markdown"
                )
                last_ui_update = time.time()
            except Exception as e:
                # Silently ignore "Message is not modified" or minor network errors
                pass

    try:
        # 1. Initialize Client & Fetcher
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred:
            await context.bot.send_message(chat_id=chat_id, text="❌ API Credentials not found.")
            return

        client = DeltaExchangeClient(api_key=cred["api_key"], api_secret=cred["api_secret"])
        
        if custom_start_ts and custom_end_ts:
            start_ts = custom_start_ts
            end_ts = custom_end_ts
            days = max(1, int((end_ts - start_ts) / 86400))
        elif days == 5000:
            # Max Available Data shortcut
            end_ts = int(datetime.utcnow().timestamp())
            start_ts = end_ts - (5000 * 86400) # Roughly 13.5 years (effectively genesis)
        else:
            end_ts = int(datetime.utcnow().timestamp())
            start_ts = end_ts - (days * 86400)
        
        # 2. Fetch Data (Phase 1)
        await _update_ui(0, 100, "Connecting to Delta Exchange...")
        fetcher = BacktestFetcher()
        csv_path, total_candles = await fetcher.fetch_and_cache(
            client=client,
            symbol=symbol,
            timeframe=timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            progress_callback=_update_ui
        )
        await client.close()

        if not csv_path or total_candles == 0:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Failed to download historical data.")
            return

        # 3. Run Simulation (Phase 2)
        await _update_ui(0, total_candles, "Warming up simulation engine...", force=True)
        engine = BacktestEngine(csv_path=csv_path, params=strategy_params)
        engine_result = await engine.run(progress_callback=_update_ui)
        
        trade_log = engine_result["trade_log"]
        equity_curve = engine_result["equity_curve"]
        
        # 4. Analytics (Phase 3)
        await _update_ui(95, 100, "Calculating advanced metrics...", force=True)
        initial_balance = strategy_params.get("initial_balance", 10000.0)
        
        metrics = calculate_metrics(trade_log, initial_balance)
        advanced_stats = run_advanced_analytics(trade_log, initial_balance)
        
        # 5. Build Final Result Document
        run_duration = time.time() - start_cpu_time
        final_result = {
            "user_id": user_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": strategy_params.get("strategy_name", "dual_supertrend"),
            "strategy_params": strategy_params,
            "direction": strategy_params.get("direction", "both"),
            "lot_size": strategy_params.get("lot_size", 1),
            "leverage": strategy_params.get("leverage", 1),
            "initial_balance": initial_balance,
            "backtest_start": datetime.fromtimestamp(start_ts, tz=timezone.utc),
            "backtest_end": datetime.fromtimestamp(end_ts, tz=timezone.utc),
            "total_candles": total_candles,
            "trade_log": trade_log,
            "equity_curve": equity_curve,
            "run_duration_seconds": run_duration,
            "created_at": datetime.utcnow()
        }
        
        # Merge metrics and stats
        final_result.update(metrics)
        final_result.update(advanced_stats)
        
        # 6. Save to MongoDB
        result_id = await save_backtest_result(final_result)
        
        # 7. Generate Files (Phase 4)
        await _update_ui(99, 100, "Generating charts and trade logs...", force=True)
        chart_path = generate_equity_curve_chart(trade_log, initial_balance, symbol, timeframe)
        csv_path = generate_trade_log_csv(trade_log, symbol, timeframe)
        
        # 8. Send Final Report
        await _send_final_report(chat_id, context, final_result, chart_path, csv_path, message_id)
        
    except Exception as e:
        logger.error(f"[BT-TASK] Fatal error during backtest: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ **Backtest Failed**\nAn unexpected error occurred:\n`{str(e)}`",
                parse_mode="Markdown"
            )
        except:
            pass


async def _send_final_report(chat_id: int, context: ContextTypes.DEFAULT_TYPE, result: dict, chart_path: str, csv_path: str, message_id: int):
    """Format and send the final Telegram report."""
    

    # Build configuration string
    params = result.get('strategy_params', {})
    config_lines = [
        f"• Strategy: {result.get('strategy', 'Unknown').replace('_', ' ').title()}",
        f"• Direction: {result.get('direction', 'both').upper()}",
        f"• Leverage: {result.get('leverage', 1)}x"
    ]
    # Add specific strategy params
    for k, v in params.items():
        if k not in ['strategy_name', 'direction', 'lot_size', 'initial_balance', 'leverage', 'paper_leverage']:
            config_lines.append(f"• {k.replace('_', ' ').title()}: {v}")
    
    config_str = "\n".join(config_lines)

    text = (
        f"📊 **Backtest Complete: {result['symbol']} ({result['timeframe']})**\n"
        f"⏱️ Analyzed {result['total_candles']:,} candles in {result['run_duration_seconds']:.1f}s\n\n"
        
        f"⚙️ **Configuration**\n"
        f"{config_str}\n\n"

        
        f"💰 **Profitability**\n"
        f"• Overall Profit: `${result['overall_profit']:.2f}` ({result['overall_profit_pct']:.2f}%)\n"
        f"• No. of Trades: `{result['num_trades']}`\n"
        f"• Win / Loss %: `{result['win_pct']:.2f}%` / `{result['loss_pct']:.2f}%`\n"
        f"• Avg Profit per Trade: `${result['avg_profit_per_trade']:.2f}`\n"
        f"• Avg Win / Avg Loss: `${result['avg_win']:.2f}` / `${result['avg_loss']:.2f}`\n"
        f"• Max Profit Single: `${result['max_profit_single']:.2f}`\n"
        f"• Max Loss Single: `${result['max_loss_single']:.2f}`\n\n"
        
        f"📉 **Risk & Drawdown**\n"
        f"• Max Drawdown: `${result['max_drawdown']:.2f}` ({result['max_drawdown_pct']:.2f}%)\n"
        f"• Duration: `{result['max_drawdown_duration_days']} days` [{result['max_drawdown_start']} to {result['max_drawdown_end']}]\n"
        f"• Max trades in drawdown: `{result['max_trades_in_drawdown']}`\n\n"
        
        f"📈 **Ratios & Streaks**\n"
        f"• Return / MaxDD: `{result['return_over_max_dd']:.2f}`\n"
        f"• Reward to Risk Ratio: `{result['reward_to_risk']:.2f}`\n"
        f"• Expectancy Ratio: `{result['expectancy_ratio']:.2f}`\n"
        f"• Max Win Streak: `{result['max_win_streak']}`\n"
        f"• Max Losing Streak: `{result['max_loss_streak']}`\n\n"
        
        f"🔮 **Advanced Analytics**\n"
        f"• R-Squared (Curve Fit): `{result['r_squared']:.3f}`\n"
        f"• Risk of Ruin (MC): `{result['monte_carlo_risk_of_ruin']:.1f}%`\n"
        f"• Sharpe Ratio: `{result['sharpe_ratio']:.2f}`\n"
    )
    
    # We delete the "loading" message and send a fresh one with the photo
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass
        
    try:
        # Send the massive text report as a separate message first (bypasses 1024 char caption limit)
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        
        # Send Photo (No long caption)
        if chart_path and os.path.exists(chart_path):
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=open(chart_path, "rb"),
                caption="📊 Equity Curve Chart",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=120
            )
            
        # Send CSV Document
        if csv_path and os.path.exists(csv_path):
            await context.bot.send_document(
                chat_id=chat_id,
                document=open(csv_path, "rb"),
                filename=os.path.basename(csv_path),
                caption="📄 Full Trade Log & Indicator Math Dump",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=120
            )
    except Exception as e:
        logger.error(f"[BT-REPORT] Error sending report: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Completed, but failed to send files due to Telegram limits.")
