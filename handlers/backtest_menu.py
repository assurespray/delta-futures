"""
Interactive Menus for the Backtester

Allows users to select a Strategy Preset, Asset, Timeframe, and Duration
in a sandbox environment before deploying anything live.
"""

import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

import os
from utils.backtest_exporter import generate_equity_curve_chart, generate_trade_log_csv
from database.crud import get_strategy_presets_by_user, get_strategy_preset_by_id, get_backtest_summary, get_backtest_results, get_backtest_result_by_id, get_api_credentials_by_user
from handlers.backtest import run_backtest_task

logger = logging.getLogger(__name__)

# FSM States
BT_SELECT_PRESET = 801
BT_SELECT_ASSET = 802
BT_SELECT_TIMEFRAME = 803
BT_SELECT_DURATION = 804
BT_CUSTOM_DATE = 805


async def menu_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main backtest menu."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = str(update.effective_user.id)
    summary = await get_backtest_summary(user_id)
    
    text = (
        "🧪 **Advanced Backtesting Sandbox**\n\n"
        "Test your mathematical strategy parameters on historical data *before* "
        "you risk live money or pollute your paper trading stats.\n\n"
        f"📊 **Your History**\n"
        f"• Total Runs: `{summary['total_backtests']}`\n"
        f"• Best Profit: `{summary['best_profit_pct']:.2f}%`\n"
        f"• Max Drawdown: `{summary['worst_drawdown_pct']:.2f}%`\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("▶️ Backtest a Strategy Preset", callback_data="bt_start_fsm")],
        [InlineKeyboardButton("🗄️ View Past Results", callback_data="bt_history")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    return ConversationHandler.END


# ==================== FSM FLOW ====================

async def bt_start_fsm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Show list of strategy presets."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    
    # Check if they have an API key (we need one just to fetch the historical data from Delta)
    creds = await get_api_credentials_by_user(user_id)
    if not creds:
        await query.edit_message_text(
            "❌ You must connect at least one Delta Exchange API Key first (so the bot can download historical data).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return ConversationHandler.END
        
    context.user_data['bt_api_id'] = str(creds[0]["_id"])  # Just use the first one available
    
    presets = await get_strategy_presets_by_user(user_id)
    
    if not presets:
        await query.edit_message_text(
            "❌ No Strategy Presets found.\nPlease go to the main menu > '🎛️ Strategy Presets' and create a mathematical configuration first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return ConversationHandler.END
        
    text = "🧪 **Step 1: Select Strategy**\n\nChoose the mathematical configuration you want to test:"
    
    keyboard = []
    for p in presets:
        keyboard.append([InlineKeyboardButton(p["preset_name"], callback_data=f"bt_pres_{p['_id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_PRESET


async def bt_preset_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Save preset and ask for asset."""
    query = update.callback_query
    await query.answer()
    
    preset_id = query.data.replace("bt_pres_", "")
    preset = await get_strategy_preset_by_id(preset_id)
    
    if not preset:
        await query.edit_message_text("❌ Preset not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]]))
        return ConversationHandler.END
        
    context.user_data['bt_preset'] = preset
    
    text = (
        f"🧪 **Step 2: Select Asset**\n\n"
        f"**Strategy:** {preset['preset_name']}\n\n"
        f"Please type the symbol of the coin you want to test (e.g. `BTCUSD`, `SOLUSD`, `DOGEUSDT`)."
    )
    
    keyboard = [
        [InlineKeyboardButton("BTCUSD", callback_data="bt_ass_BTCUSD"),
         InlineKeyboardButton("ETHUSD", callback_data="bt_ass_ETHUSD")],
        [InlineKeyboardButton("SOLUSD", callback_data="bt_ass_SOLUSD"),
         InlineKeyboardButton("DOGEUSD", callback_data="bt_ass_DOGEUSD")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_ASSET


async def bt_asset_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2b: User clicked an asset button."""
    query = update.callback_query
    await query.answer()
    
    asset = query.data.replace("bt_ass_", "")
    context.user_data['bt_asset'] = asset
    
    return await bt_ask_timeframe(query, context)


async def bt_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2b: User typed an asset symbol."""
    asset = update.message.text.strip().upper()
    
    if len(asset) < 3:
        await update.message.reply_text("❌ Invalid symbol. Try again:")
        return BT_SELECT_ASSET
        
    context.user_data['bt_asset'] = asset
    
    # Reply with a new menu
    message = await update.message.reply_text("Processing...", parse_mode="Markdown")
    context.user_data['bt_msg_id'] = message.message_id
    
    # Convert context to mock query-like object for rendering the next menu
    class MockQuery:
        def __init__(self, msg):
            self.message = msg
        async def edit_message_text(self, *args, **kwargs):
            return await self.message.edit_text(*args, **kwargs)
            
    return await bt_ask_timeframe(MockQuery(message), context)


async def bt_ask_timeframe(query, context: ContextTypes.DEFAULT_TYPE):
    """Step 3: Ask for timeframe."""
    preset_name = context.user_data['bt_preset']['preset_name']
    asset = context.user_data['bt_asset']
    
    text = (
        f"🧪 **Step 3: Select Timeframe**\n\n"
        f"**Strategy:** {preset_name}\n"
        f"**Asset:** {asset}\n\n"
        f"Choose the chart timeframe to simulate:"
    )
    
    keyboard = [
        [InlineKeyboardButton("1m", callback_data="bt_tf_1m"), InlineKeyboardButton("3m", callback_data="bt_tf_3m"), InlineKeyboardButton("5m", callback_data="bt_tf_5m")],
        [InlineKeyboardButton("15m", callback_data="bt_tf_15m"), InlineKeyboardButton("30m", callback_data="bt_tf_30m"), InlineKeyboardButton("1h", callback_data="bt_tf_1h")],
        [InlineKeyboardButton("4h", callback_data="bt_tf_4h"), InlineKeyboardButton("1d", callback_data="bt_tf_1d")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_TIMEFRAME


async def bt_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Ask for duration."""
    query = update.callback_query
    await query.answer()
    
    tf = query.data.replace("bt_tf_", "")
    context.user_data['bt_timeframe'] = tf
    
    preset_name = context.user_data['bt_preset']['preset_name']
    asset = context.user_data['bt_asset']
    
    text = (
        f"🧪 **Step 4: Select Duration**\n\n"
        f"**Strategy:** {preset_name}\n"
        f"**Asset:** {asset}\n"
        f"**Timeframe:** {tf}\n\n"
        f"Choose how much historical data to download and test:"
    )
    
    keyboard = [
        [InlineKeyboardButton("7 Days", callback_data="bt_dur_7"), InlineKeyboardButton("30 Days", callback_data="bt_dur_30")],
        [InlineKeyboardButton("90 Days", callback_data="bt_dur_90"), InlineKeyboardButton("180 Days", callback_data="bt_dur_180")],
        [InlineKeyboardButton("1 Year", callback_data="bt_dur_365"), InlineKeyboardButton("2 Years", callback_data="bt_dur_730")],
        [InlineKeyboardButton("♾️ Max Available Data", callback_data="bt_dur_5000")],
        [InlineKeyboardButton("📅 Custom Date Range", callback_data="bt_dur_custom")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_DURATION


async def bt_duration_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Launch the background task or ask for custom date."""
    query = update.callback_query
    await query.answer()
    
    action = query.data.replace("bt_dur_", "")
    
    if action == "custom":
        text = (
            "📅 **Custom Date Range**\n\n"
            "Please type the start and end dates you want to test.\n"
            "Format: `YYYY-MM-DD to YYYY-MM-DD`\n"
            "Example: `2023-01-01 to 2024-01-01`"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]]), parse_mode="Markdown")
        return BT_CUSTOM_DATE

    days = int(action)
    return await _launch_backtest_task(query.message.message_id, update.effective_chat.id, update.effective_user.id, context, days=days)


async def bt_custom_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process custom date string and launch."""
    text = update.message.text.strip()
    
    try:
        parts = text.split(" to ")
        if len(parts) != 2:
            raise ValueError()
        
        start_date = datetime.strptime(parts[0].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(parts[1].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        if start_date >= end_date:
            await update.message.reply_text("❌ Start date must be before end date. Try again:")
            return BT_CUSTOM_DATE
            
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        if end_date > now:
            end_date = now
            
        # Calculate days for the fetcher logic (which still uses days backward implicitly, wait we can just pass specific start/end timestamps to the task!)
        # Actually our task signature takes `days`. Let's pass the exact days difference.
        delta = end_date - start_date
        days = delta.days
        
        # We need the bot to stop at the start_date we requested.
        # But `run_backtest_task` calculates: end_ts = now, start_ts = end_ts - days
        # To support absolute start/end, we should pass start_ts and end_ts explicitly to `run_backtest_task`!
        # Let's just calculate the offset. If end_date is 30 days ago, and start_date is 60 days ago...
        # It's cleaner to update `run_backtest_task` to take `start_ts` and `end_ts` optionally.
        
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Please use `YYYY-MM-DD to YYYY-MM-DD` (e.g. `2023-01-01 to 2024-01-01`):", parse_mode="Markdown")
        return BT_CUSTOM_DATE
        
    message = await update.message.reply_text("Processing...", parse_mode="Markdown")
    return await _launch_backtest_task(message.message_id, update.effective_chat.id, update.effective_user.id, context, start_ts=int(start_date.timestamp()), end_ts=int(end_date.timestamp()))


async def _launch_backtest_task(message_id, chat_id, user_id, context, days=None, start_ts=None, end_ts=None):
    preset = context.user_data['bt_preset']
    asset = context.user_data['bt_asset']
    timeframe = context.user_data['bt_timeframe']
    api_id = context.user_data['bt_api_id']
    
    strategy_params = {
        "strategy_name": preset.get("strategy_type", "dual_supertrend"),
        "direction": preset.get("parameters", {}).get("direction", "both"),
        "lot_size": 1,
        "initial_balance": 10000.0,
        "leverage": 10
    }
    if "parameters" in preset:
        strategy_params.update(preset["parameters"])
        
    loading_text = f"🧪 **Initializing Sandbox Engine...**\n\nAsset: {asset}\nTimeframe: {timeframe}\n\n⏳ Please wait..."
    
    # We edit the specific message_id to show loading status
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=loading_text, parse_mode="Markdown")
    except:
        pass
    
    task = asyncio.create_task(
        run_backtest_task(
            chat_id=chat_id,
            message_id=message_id,
            context=context,
            user_id=str(user_id),
            api_id=api_id,
            symbol=asset,
            timeframe=timeframe,
            days=days,
            strategy_params=strategy_params,
            custom_start_ts=start_ts,
            custom_end_ts=end_ts
        )
    )
    
    # Store task reference so the stop button can cancel it
    context.user_data['bt_running_task'] = task
    
    return ConversationHandler.END


# ==================== HISTORY MENU ====================

async def bt_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated history of backtests."""
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    
    # Always sort by newest for the basic history view
    results = await get_backtest_results(user_id, sort_by="created_at", sort_order=-1, limit=5)
    
    if not results:
        await query.edit_message_text(
            "🗄️ No past backtest results found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return
        
    text = "🗄️ **Recent Backtest Results**\n\nSelect a result to view its full details:"
    
    keyboard = []
    for r in results:
        dt = r["created_at"].strftime('%Y-%m-%d %H:%M')
        label = f"{r.get('symbol', '?')} {r.get('timeframe', '?')} | PnL: {r.get('overall_profit_pct', 0):.1f}% | {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"bt_view_{r['_id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Backtester", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_view_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View details of a specific past backtest."""
    query = update.callback_query
    await query.answer()
    
    result_id = query.data.replace("bt_view_", "")
    r = await get_backtest_result_by_id(result_id)
    
    if not r:
        await query.edit_message_text(
            "❌ Result not found or deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="bt_history")]])
        )
        return
        
    # Build Configuration block
    params = r.get('strategy_params', {})
    config_lines = [
        f"• Strategy: {r.get('strategy', 'Unknown').replace('_', ' ').title()}",
        f"• Direction: {r.get('direction', 'both').upper()}",
    ]
    for k, v in params.items():
        if k not in ['strategy_name', 'direction', 'lot_size', 'initial_balance', 'leverage', 'paper_leverage']:
            config_lines.append(f"• {k.replace('_', ' ').title()}: {v}")
    config_str = "\n".join(config_lines)

    rs = r.get('rolling_stats') or {}
    m = rs.get('monthly') or {}
    
    text = (
        f"📊 **Backtest Record: {r.get('symbol', 'Unknown')} ({r.get('timeframe', 'Unknown')})**\n"
        f"Run Date: `{r.get('created_at', 'Unknown')}`\n\n"
        f"⚙️ **Configuration**\n{config_str}\n\n"
        f"🏦 **Avg Safe Margin:** `${r.get('avg_max_margin_required', 0):.2f}`\n\n"
        f"💰 **Profit:** `${r.get('overall_profit', 0):.2f}` ({r.get('overall_profit_pct', 0):.2f}%)\n"
        f"📉 **Max DD:** `${r.get('max_drawdown', 0):.2f}` ({r.get('max_drawdown_pct', 0):.2f}%)\n"
        f"🎯 **Win Rate:** `{r.get('win_pct', 0):.2f}%`\n"
        f"🔄 **Monthly Profitable:** `{m.get('win_rate', 0):.1f}%`\n"
        f"🔮 **R-Squared:** `{r.get('r_squared', 0):.3f}`\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📥 Resend Chart & CSV", callback_data=f"bt_resend_{result_id}")],
        [InlineKeyboardButton("📖 Glossary & Benchmarks", callback_data="bt_glossary")],
        [InlineKeyboardButton("🗑️ Delete Record", callback_data=f"bt_del_{result_id}")],
        [InlineKeyboardButton("🔙 Back to History", callback_data="bt_history")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")



async def bt_resend_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend the chart and CSV files for a past backtest."""
    query = update.callback_query
    await query.answer("Generating files, please wait...")
    
    result_id = query.data.replace("bt_resend_", "")
    r = await get_backtest_result_by_id(result_id)
    
    if not r:
        await query.message.reply_text("❌ Result not found or deleted.")
        return
        
    trade_log = r.get("trade_log", [])
    if not trade_log:
        await query.message.reply_text("❌ No trade log data available for this backtest.")
        return
        
    symbol = r.get("symbol", "Unknown")
    timeframe = r.get("timeframe", "Unknown")
    initial_balance = r.get("initial_balance", 10000.0)
    
    # Generate files
    chart_path = generate_equity_curve_chart(trade_log, initial_balance, symbol, timeframe)
    csv_path = generate_trade_log_csv(trade_log, symbol, timeframe)
    
    chat_id = update.effective_chat.id
    
    try:
        # Send Photo
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, "rb") as photo_file:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=f"📊 Equity Curve Chart: {symbol} ({timeframe})",
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
                    caption=f"📄 Full Trade Log: {symbol} ({timeframe})",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[BT-RESEND] Error sending files: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Failed to send files due to Telegram limits.")
    finally:
        # Cleanup temp files
        for fpath in [chart_path, csv_path]:
            if fpath and os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass


async def bt_del_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a specific backtest record."""
    query = update.callback_query
    await query.answer()
    
    from database.crud import delete_backtest_result
    result_id = query.data.replace("bt_del_", "")
    await delete_backtest_result(result_id)
    
    # Go back to history
    await bt_history_menu(update, context)


async def bt_stop_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a running backtest task."""
    query = update.callback_query
    await query.answer("Stopping...")
    
    task = context.user_data.get('bt_running_task')
    if task and not task.done():
        task.cancel()
        context.user_data.pop('bt_running_task', None)
    else:
        # Task already finished or no task found
        try:
            await query.edit_message_text("ℹ️ No backtest is currently running.")
        except:
            pass


async def bt_glossary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send glossary and benchmark explanations."""
    query = update.callback_query
    await query.answer()
    
    text = (
        "📖 **Backtest Glossary & Benchmarks**\n\n"
        
        "📊 **Expectancy Ratio**\n"
        "How much you expect to win on average per dollar risked.\n"
        "• `< 0.20` (Poor): Lacks an edge. Vulnerable to fees & slippage.\n"
        "• `0.20 - 0.50` (Good): Sweet spot for sustainable algorithmic/swing strategies.\n"
        "• `0.50 - 1.00` (Excellent): Exceptional edge, usually long-term trend following.\n"
        "• `> 1.00` (Suspicious): Highly likely to be overfitted or look-ahead biased.\n\n"
        
        "⚖️ **Reward to Risk Ratio**\n"
        "Average Win divided by Average Loss.\n"
        "• `> 1.5`: Ideal for most breakout strategies.\n"
        "• `< 1.0`: You lose more when wrong than you make when right. Requires a very high win rate to survive.\n\n"
        
        "📈 **R-Squared (Curve Fit)**\n"
        "Measures how smooth your equity curve is. `1.0` is a perfect straight line up.\n"
        "• `> 0.80`: Smooth, steady growth. Highly robust.\n"
        "• `< 0.50`: Choppy, volatile. Profits come from a few lucky spikes.\n\n"
        
        "🛡️ **Sharpe & Sortino Ratios**\n"
        "Measures Return vs. Volatility (Risk-adjusted return).\n"
        "• **Sharpe**: Penalizes both upside AND downside volatility. `> 1.0` is great.\n"
        "• **Sortino**: Better for crypto. Only penalizes downside volatility (losing streaks). `> 1.5` is excellent.\n\n"
        
        "🎲 **Monte Carlo (Risk of Ruin)**\n"
        "We shuffle your trades randomly 1,000 times to simulate alternate realities.\n"
        "• **Risk of Ruin**: % chance your account would hit a 50% drawdown in a randomized future.\n"
        "• **Worst-Case 95%/99%**: The maximum drawdown reached in 95% and 99% of those simulated realities. This is your true worst-case scenario."
    )
    
    keyboard = [
        [InlineKeyboardButton("🔙 Back to Backtest Menu", callback_data="menu_backtest")]
    ]

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def get_backtest_handlers():
    """Return all handlers for backtesting."""
    
    fsm_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(bt_start_fsm, pattern="^bt_start_fsm$")],
        states={
            BT_SELECT_PRESET: [
                CallbackQueryHandler(bt_preset_selected, pattern="^bt_pres_")
            ],
            BT_SELECT_ASSET: [
                CallbackQueryHandler(bt_asset_selected_callback, pattern="^bt_ass_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bt_asset_received)
            ],
            BT_SELECT_TIMEFRAME: [
                CallbackQueryHandler(bt_timeframe_selected, pattern="^bt_tf_")
            ],
            BT_SELECT_DURATION: [
                CallbackQueryHandler(bt_duration_selected, pattern="^bt_dur_")
            ],
            BT_CUSTOM_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bt_custom_date_received)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(menu_backtest, pattern="^menu_backtest$"),
            CallbackQueryHandler(menu_backtest, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    
    return [
        fsm_handler,
        CallbackQueryHandler(menu_backtest, pattern="^menu_backtest$"),
        CallbackQueryHandler(bt_history_menu, pattern="^bt_history$"),
        CallbackQueryHandler(bt_view_result, pattern="^bt_view_"),
        CallbackQueryHandler(bt_resend_result, pattern="^bt_resend_"),
        CallbackQueryHandler(bt_del_result, pattern="^bt_del_"),
        CallbackQueryHandler(bt_stop_backtest, pattern="^bt_stop$"),
        CallbackQueryHandler(bt_glossary, pattern="^bt_glossary$"),
    ]
