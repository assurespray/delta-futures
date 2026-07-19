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

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
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

import numpy as np

def recalculate_metrics_with_auto_capital(trade_log: list, leverage: float):
    """
    Recalculates trade margins for a specific leverage,
    calculates Auto-Capital (Peak Margin + Max DD),
    and re-runs the advanced metrics.
    Modifies trade_log IN PLACE.
    """
    if not trade_log:
        return 100.0, 0.0, 0.0, calculate_metrics([], 100.0), run_advanced_analytics([], 100.0)
        
    # First, calculate absolute Max Drawdown USD
    pnls = [t["pnl"] for t in trade_log]
    cumulative = np.cumsum(pnls)
    peaks = np.maximum.accumulate(cumulative)
    drawdowns = peaks - cumulative
    max_dd_usd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    
    # Recalculate margins per trade based on new leverage
    peak_margin = 0.0
    for t in trade_log:
        notional = t.get("notional_size", 0.0)
        old_im = t.get("initial_margin", 0.0)
        old_max_m = t.get("max_margin_required", 0.0)
        sl_risk = max(0.0, old_max_m - old_im)
        
        new_im = notional / leverage if leverage > 0 else notional
        new_max_m = new_im + sl_risk
        
        t["initial_margin"] = new_im
        t["max_margin_required"] = new_max_m
        t["roe_pct"] = (t["pnl"] / new_im) * 100.0 if new_im > 0 else 0.0
        
        if new_max_m > peak_margin:
            peak_margin = new_max_m
            
    auto_capital = peak_margin + max_dd_usd
    if auto_capital <= 0:
        auto_capital = 100.0 # Safety fallback
        
    metrics = calculate_metrics(trade_log, auto_capital)
    advanced = run_advanced_analytics(trade_log, auto_capital)
    
    return auto_capital, peak_margin, max_dd_usd, metrics, advanced


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
        "eta": "Calculating...",
        "batch_completed": [],
        "batch_current_tf": None,
        "batch_pending": []
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
            
            if timeframe == "batch_native":
                strat_label = strategy_params.get('strategy_name', '').replace('_', ' ').title()
                lines = [f"🧪 **Batch Backtest: {symbol} ({days}d)**", f"Strategy: {strat_label}", ""]
                
                for c in ui_state["batch_completed"]:
                    lines.append(f"✅ **{c['tf']}** → {c['pct']:+.1f}% {c['icon']}")
                
                cur = ui_state.get("batch_current_tf")
                if cur:
                    lines.append(f"▶️ **{cur}** → {status_msg} {bar}")
                
                for p in ui_state.get("batch_pending", []):
                    lines.append(f"⏳ **{p}**")
                
                lines.append(f"\n**ETA:** {ui_state['eta']}")
                text = chr(10).join(lines)
            else:
                display_tf = timeframe
                text = (
                    f"🧪 **Backtest in Progress**\n\n"
                    f"**Asset:** {symbol} | **TF:** {display_tf} | **Days:** {days}\n"
                    f"**Status:** {status_msg}\n\n"
                    f"**Progress:** {bar}\n"
                    f"**ETA:** {ui_state['eta']}\n\n"
                    f"⏳ _Please wait, doing heavy math in background..._"
                )
            
            stop_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 Stop Backtest", callback_data="bt_stop")]
            ])
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=stop_keyboard
                )
                last_ui_update = time.time()
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.warning(f"[BT-UI] Progress update failed: {e}")


    try:
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred:
            await context.bot.send_message(chat_id=chat_id, text="❌ API Credentials not found.")
            return

        if custom_start_ts and custom_end_ts:
            start_ts = custom_start_ts
            end_ts = custom_end_ts
            days = max(1, int((end_ts - start_ts) / 86400))
        elif days == 5000:
            end_ts = int(datetime.utcnow().timestamp())
            start_ts = end_ts - (5000 * 86400)
        else:
            end_ts = int(datetime.utcnow().timestamp())
            start_ts = end_ts - (days * 86400)

        # Batch Logic
        timeframes_to_run = [timeframe]
        if timeframe == "batch_native":
            from config.constants import SUPPORTED_NATIVE_TIMEFRAMES
            timeframes_to_run = SUPPORTED_NATIVE_TIMEFRAMES
            
        batch_results = []
        
        for tf in timeframes_to_run:
            if timeframe == "batch_native":
                idx = timeframes_to_run.index(tf)
                ui_state["batch_current_tf"] = tf
                ui_state["batch_pending"] = timeframes_to_run[idx + 1:]
                await _update_ui(0, 100, f"Starting batch run for {tf}...", force=True)
                
            client = DeltaExchangeClient(api_key=cred["api_key"], api_secret=cred["api_secret"])
            
            # Fetch
            await _update_ui(0, 100, f"Connecting to Delta Exchange for {tf}...")
            fetcher = BacktestFetcher()
            csv_path, total_candles = await fetcher.fetch_and_cache(
                client=client, symbol=symbol, timeframe=tf,
                start_ts=start_ts, end_ts=end_ts, progress_callback=_update_ui
            )
            await client.close()

            if not csv_path or total_candles == 0:
                if timeframe != "batch_native":
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ Failed to download historical data for {tf}.")
                    return
                continue

            # Run Engine
            await _update_ui(0, total_candles, f"Warming up engine for {tf}...", force=True)
            sp = dict(strategy_params)
            sp["symbol"] = symbol
            sp["timeframe"] = tf
            engine = BacktestEngine(csv_path=csv_path, params=sp)
            engine_result = await engine.run(progress_callback=_update_ui)
            
            trade_log = engine_result["trade_log"]
            equity_curve = engine_result["equity_curve"]
            
            await _update_ui(95, 100, f"Calculating metrics for {tf}...", force=True)
            
            from utils.market_utils import get_max_leverage
            max_lev = get_max_leverage(symbol)
            base_leverage = min(200.0, max_lev)
            
            auto_cap, peak_m, max_dd_usd, metrics, advanced_stats = recalculate_metrics_with_auto_capital(trade_log, base_leverage)
            
            run_duration = time.time() - start_cpu_time
            
            db_trade_log = trade_log[:2500] if len(trade_log) > 2500 else trade_log
            db_equity_curve = equity_curve[:2500] if len(equity_curve) > 2500 else equity_curve
                
            final_result = {
                "user_id": user_id,
                "symbol": symbol,
                "timeframe": tf,
                "strategy": sp.get("strategy_name", "dual_supertrend"),
                "strategy_params": sp,
                "direction": sp.get("direction", "both"),
                "lot_size": sp.get("lot_size", 1),
                "leverage": base_leverage,
                "initial_balance": auto_cap,
                "backtest_start": datetime.fromtimestamp(start_ts, tz=timezone.utc),
                "backtest_end": datetime.fromtimestamp(end_ts, tz=timezone.utc),
                "total_candles": total_candles,
                "trade_log": db_trade_log,
                "equity_curve": db_equity_curve,
                "run_duration_seconds": run_duration,
                "created_at": datetime.utcnow()
            }
            
            final_result.update(metrics)
            final_result.update(advanced_stats)
            
            result_id = await save_backtest_result(final_result)
            final_result['_id'] = result_id
            
            batch_results.append(final_result)
            
            if timeframe == "batch_native":
                pct = final_result.get('overall_profit_pct', 0)
                icon = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
                ui_state["batch_completed"].append({"tf": tf, "pct": pct, "icon": icon})
            else:
                # Generate Files and Send Report for single
                await _update_ui(99, 100, "Generating charts and trade logs...", force=True)
                chart_path = generate_equity_curve_chart(trade_log, auto_cap, symbol, tf, result=final_result)
                csv_path = generate_trade_log_csv(trade_log, symbol, tf)
                await _send_final_report(chat_id, context, final_result, chart_path, csv_path, message_id)
                return

        # If we get here, it's a batch run
        if timeframe == "batch_native" and batch_results:
            strat_name = strategy_params.get('strategy_name', 'Unknown').replace('_', ' ').title()
            summary_lines = [f"✅ **Batch Backtest Complete for {symbol}**", f"Strategy: {strat_name}", "", "**Performance Summary:**"]
            for r in batch_results:
                icon = "🟢" if r.get('overall_profit_pct', 0) > 0 else "🔴"
                if r.get('overall_profit_pct', 0) == 0: icon = "⚪"
                summary_lines.append(f"• **{r['timeframe']}:** {r.get('overall_profit_pct', 0):+.1f}% (W: {r.get('win_pct', 0):.1f}%) {icon}")
            
            context.user_data['bt_batch_result_ids'] = [str(r['_id']) for r in batch_results]
            
            keyboard = [
                [InlineKeyboardButton("🗄️ View Full Reports & Charts", callback_data="bt_batch_results")],
                [InlineKeyboardButton("🔙 Back to Backtest Menu", callback_data="menu_backtest")]
            ]
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=chr(10).join(summary_lines),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"[BT-TASK] Failed to send batch summary: {e}")
    except asyncio.CancelledError:
        logger.info(f"[BT-TASK] Backtest cancelled by user: {symbol} {timeframe}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="🛑 **Backtest Stopped**\n\nThe backtest was cancelled by user request.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"[BT-TASK] Failed to send cancel message: {e}")
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
        except Exception as e:
            logger.warning(f"[BT-TASK] Failed to send error message: {e}")


def format_report_text(result: dict) -> str:
    params = result.get('strategy_params', {})
    
    config_lines = [
        f"• Strategy: {result.get('strategy', 'Unknown').replace('_', ' ').title()}",
        f"• Direction: {result.get('direction', 'both').upper()}"
    ]
    for k, v in params.items():
        if k not in ['strategy_name', 'direction', 'lot_size', 'initial_balance', 'leverage', 'paper_leverage']:
            config_lines.append(f"• {k.replace('_', ' ').title()}: {v}")
    
    config_str = "\n".join(config_lines)

    rs = result.get('rolling_stats') or {}
    w = rs.get('weekly') or {}
    m = rs.get('monthly') or {}
    
    rolling_str = (
        f"🔄 **Rolling Consistency**\n"
        f"• Profitable: `{w.get('win_rate', 0):.1f}%` (Wk) | `{m.get('win_rate', 0):.1f}%` (Mo)\n"
        f"• Best Wk: `${w.get('best_usd', 0):+.2f}` ({w.get('best', 0):+.1f}%)\n"
        f"• Best Mo: `${m.get('best_usd', 0):+.2f}` ({m.get('best', 0):+.1f}%)\n"
        f"• Worst Wk: `${w.get('worst_usd', 0):+.2f}` ({w.get('worst', 0):+.1f}%)\n"
        f"• Worst Mo: `${m.get('worst_usd', 0):+.2f}` ({m.get('worst', 0):+.1f}%)\n\n"
    )
    
    lev = result.get('leverage', 1)
    
    text = (
        f"📊 **Backtest Complete: {result['symbol']} ({result['timeframe']})**\n"
        f"⏱️ Analyzed {result['total_candles']:,} candles in {result['run_duration_seconds']:.1f}s\n\n"
        
        f"⚙️ **Configuration**\n"
        f"{config_str}\n\n"
        
        f"🏦 **Capital Required (Auto-Sized for {int(lev)}x Lev)**\n"
        f"• Sizing: `{result.get('lot_size', 0)} Contracts` (Avg Notional: `${result.get('avg_notional_size', 0):.2f}`)\n"
        f"• Initial Margin (Avg): `${result.get('avg_initial_margin', 0):.2f}`\n"
        f"• Stop-Loss Buffer (Peak): `${max(0.0, result.get('peak_margin_required', 0) - result.get('avg_initial_margin', 0)):.2f}`\n"
        f"• Max Historical Drawdown: `${abs(result.get('max_drawdown', 0)):.2f}`\n"
        f"• Recommended Deposit: `${result.get('initial_balance', 0):.2f}`\n\n"

        f"💰 **Profitability**\n"
        f"• Overall Net Profit: `${result['overall_profit']:.2f}` ({result['overall_profit_pct']:.2f}%)\n"
        f"• Gross Profit: `${result.get('total_gross_profit', result['overall_profit']):.2f}` | Total Fees: `${-result.get('total_fees_paid', 0.0):.2f}`\n"
        f"• Est. Slippage: `-${result.get('total_slippage_penalty', 0.0):.2f}`\n"
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
        f"• Reward to Risk Ratio: `{result['reward_to_risk']:.2f}` *(Ideal: > 1.5)*\n"
        f"• Expectancy Ratio: `{result['expectancy_ratio']:.2f}` *(Ideal: 0.20 - 0.50)*\n"
        f"• Return / MaxDD: `{result['return_over_max_dd']:.2f}`\n"
        f"• Max Win Streak: `{result['max_win_streak']}`\n"
        f"• Max Losing Streak: `{result['max_loss_streak']}`\n\n"
        
        + rolling_str +
        
        f"🎲 **Monte Carlo Simulations (1,000 runs)**\n"
        f"• Risk of Ruin: `{result['monte_carlo_risk_of_ruin']:.1f}%`\n"
        f"• Worst-Case Drawdown (95% prob): `${-result.get('monte_carlo_max_dd_95_usd', 0):.2f}` ({result.get('monte_carlo_max_dd_95_pct', 0):.2f}%)\n"
        f"• Worst-Case Drawdown (99% prob): `${-result.get('monte_carlo_max_dd_99_usd', 0):.2f}` ({result.get('monte_carlo_max_dd_99_pct', 0):.2f}%)\n\n"
        
        f"🔮 **Advanced Analytics**\n"
        f"• R-Squared: `{result['r_squared']:.3f}` *(Ideal: > 0.80)*\n"
        f"• Sharpe Ratio: `{result['sharpe_ratio']:.2f}` *(Ideal: > 1.0)*\n"
        f"• Sortino Ratio: `{result.get('sortino_ratio', 0.0):.2f}`\n"
    )
    return text

async def _send_final_report(chat_id: int, context: ContextTypes.DEFAULT_TYPE, result: dict, chart_path: str, csv_path: str, message_id: int):
    """Format and send the final Telegram report."""
    text = format_report_text(result)
    
    # We delete the "loading" message and send a fresh one with the photo
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"[BT-REPORT] Could not delete loading message: {e}")
        
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from utils.market_utils import get_max_leverage
        
        max_lev = get_max_leverage(result['symbol'])
        
        # Build dynamic 9-button grid
        std_tiers = [1, 2, 3, 5, 10, 25, 50, 100, 200]
        valid_tiers = [t for t in std_tiers if t <= max_lev]
        if max_lev not in valid_tiers:
            valid_tiers.append(int(max_lev))
            valid_tiers.sort()
            
        btn_rows = []
        current_row = []
        for t in valid_tiers:
            current_row.append(InlineKeyboardButton(f"🔍 {t}x", callback_data=f"bt_recalc_{result.get('_id', '')}_{t}"))
            if len(current_row) >= 5:
                btn_rows.append(current_row)
                current_row = []
        if current_row:
            btn_rows.append(current_row)
            
        # We don't include "All Trades" since it is default on fresh result
        result_id = result.get('_id', '')
        dir_filter_row = [
            InlineKeyboardButton("📈 Long Only", callback_data=f"bt_dirfilter_{result_id}_long"),
            InlineKeyboardButton("📉 Short Only", callback_data=f"bt_dirfilter_{result_id}_short")
        ]
            
        keyboard = btn_rows + [
            dir_filter_row,
            [InlineKeyboardButton("📖 Glossary & Benchmarks", callback_data="bt_glossary")],
            [InlineKeyboardButton("🔄 Backtest Another Strategy", callback_data="bt_start_fsm")],
            [InlineKeyboardButton("🔙 Back to Backtest Menu", callback_data="menu_backtest")]
        ]
        
        # Send the massive text report as a separate message first (bypasses 1024 char caption limit)
        await context.bot.send_message(
            chat_id=chat_id, 
            text=text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Send Photo (No long caption)
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, "rb") as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption="📊 Equity Curve Chart",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
            
        # Send CSV Document
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, "rb") as csv_file:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=csv_file,
                    filename=os.path.basename(csv_path),
                    caption="📄 Full Trade Log & Indicator Math Dump",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
    except Exception as e:
        logger.error(f"[BT-REPORT] Error sending report: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Completed, but failed to send files due to Telegram limits.")
    finally:
        # Cleanup temp files from ephemeral disk
        for path in [chart_path, csv_path]:
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
