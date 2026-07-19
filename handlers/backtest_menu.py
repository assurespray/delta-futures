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
from database.crud import get_strategy_presets_by_user, get_strategy_preset_by_id, get_backtest_summary, get_backtest_results, get_backtest_results_by_ids, get_backtest_result_by_id, get_api_credentials_by_user
from handlers.backtest import run_backtest_task

logger = logging.getLogger(__name__)

# FSM States
BT_SELECT_PRESET = 801
BT_SELECT_ASSET = 802
BT_SELECT_TIMEFRAME = 803
BT_SELECT_DURATION = 804
BT_CUSTOM_DATE = 805
BT_SELECT_LOT_SIZE = 807
BT_ASK_TIME_MODE = 808
BT_ASK_CUSTOM_TIME = 809
BT_ASK_CUSTOM_TIMEFRAME = 810


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
        [InlineKeyboardButton("🌐 Batch Test All (8 Native)", callback_data="bt_tf_batch_native")],
        [InlineKeyboardButton("1m", callback_data="bt_tf_1m"), InlineKeyboardButton("3m", callback_data="bt_tf_3m"), InlineKeyboardButton("5m", callback_data="bt_tf_5m")],
        [InlineKeyboardButton("15m", callback_data="bt_tf_15m"), InlineKeyboardButton("30m", callback_data="bt_tf_30m"), InlineKeyboardButton("1h", callback_data="bt_tf_1h")],
        [InlineKeyboardButton("4h", callback_data="bt_tf_4h"), InlineKeyboardButton("1d", callback_data="bt_tf_1d")],
        [InlineKeyboardButton("⚙️ Custom Timeframe", callback_data="bt_tf_custom")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_TIMEFRAME


async def bt_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Ask for Lot Size (or Custom TF)."""
    query = update.callback_query
    await query.answer()
    
    tf = query.data.replace("bt_tf_", "")
    
    if tf == "custom":
        await query.edit_message_text(
            "⚙️ **Custom Timeframe**\n\n"
            "Please type your custom timeframe.\n"
            "Format: Number followed by `m`, `h`, or `d`.\n"
            "Examples: `10m`, `2h`, `45m`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]])
        )
        return BT_ASK_CUSTOM_TIMEFRAME
        
    context.user_data['bt_timeframe'] = tf
    return await bt_ask_lot_size(update, context)

import re

async def bt_custom_timeframe_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process custom timeframe input."""
    text = update.message.text.strip().lower()
    
    if not re.match(r"^\d+[mhd]$", text):
        await update.message.reply_text(
            "❌ **Invalid Format**\n\nPlease enter a valid timeframe (e.g., `10m`, `2h`, `1d`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]])
        )
        return BT_ASK_CUSTOM_TIMEFRAME
        
    context.user_data['bt_timeframe'] = text
    return await bt_ask_lot_size(update, context)

async def bt_ask_lot_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 5: Ask for Lot Size."""
    query = update.callback_query
    message = update.message if update.message else query.message
    
    preset_name = context.user_data['bt_preset']['preset_name']
    asset = context.user_data['bt_asset']
    tf = context.user_data['bt_timeframe']
    display_tf = "Batch (8 Timeframes)" if tf == "batch_native" else tf
    
    text = (
        f"🧪 **Step 4: Lot Size**\n\n"
        f"**Strategy:** {preset_name}\n"
        f"**Asset:** {asset} ({display_tf})\n\n"
        f"How many lots (contracts) do you want to trade per signal?\n"
        f"*(Select an option or type a custom number)*"
    )
    keyboard = [
        [InlineKeyboardButton("1 Lot", callback_data="bt_lot_1"), InlineKeyboardButton("10 Lots", callback_data="bt_lot_10")],
        [InlineKeyboardButton("100 Lots", callback_data="bt_lot_100"), InlineKeyboardButton("1000 Lots", callback_data="bt_lot_1000")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    return BT_SELECT_LOT_SIZE


async def bt_lot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 5b: Handle Lot Button Click"""
    query = update.callback_query
    await query.answer()
    lot = query.data.replace("bt_lot_", "")
    context.user_data['bt_lot_size'] = float(lot)
    return await bt_ask_time_mode(query, context)


async def bt_lot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 5b: Handle Custom Lot Typed"""
    val = update.message.text.strip()
    try:
        lot = float(val)
        if lot <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive number for lot size:")
        return BT_SELECT_LOT_SIZE
        
    context.user_data['bt_lot_size'] = lot
    message = await update.message.reply_text("Processing...", parse_mode="Markdown")
    
    class MockQuery:
        def __init__(self, msg):
            self.message = msg
        async def edit_message_text(self, *args, **kwargs):
            return await self.message.edit_text(*args, **kwargs)
            
    return await bt_ask_time_mode(MockQuery(message), context)


async def bt_ask_time_mode(query, context: ContextTypes.DEFAULT_TYPE):
    """Step 6: Ask for Time Mode."""
    text = (
        f"🧪 **Step 6: Time Window**\n\n"
        f"Do you want to run this strategy 24/7, or restrict it to a specific Time Window (IST)?"
    )
    keyboard = [
        [InlineKeyboardButton("🌍 Run 24/7", callback_data="bt_time_247")],
        [InlineKeyboardButton("🕒 Custom Time Window", callback_data="bt_time_custom")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_ASK_TIME_MODE


async def bt_time_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 6b: Handle Time Mode Selection"""
    query = update.callback_query
    await query.answer()
    
    mode = query.data.replace("bt_time_", "")
    
    if mode == "247":
        context.user_data['bt_time_window'] = None
        return await bt_ask_duration(query, context)
    else:
        text = (
            f"🕒 **Custom Time Window (IST)**\n\n"
            f"Please reply with your times in `HH:MM` format separated by commas:\n"
            f"`Start Time, Stop Entries, Hard Exit`\n\n"
            f"Example (8 PM to 9 PM):\n"
            f"`20:00, 20:45, 21:00`"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")]]), parse_mode="Markdown")
        return BT_ASK_CUSTOM_TIME


async def bt_custom_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 6c: Handle Custom Time String"""
    val = update.message.text.strip()
    try:
        parts = [p.strip() for p in val.split(",")]
        if len(parts) != 3:
            raise ValueError
            
        from utils.time_utils import parse_time
        # Validate they are parseable
        t_start = parse_time(parts[0])
        t_stop = parse_time(parts[1])
        t_exit = parse_time(parts[2])
        
        context.user_data['bt_time_window'] = {
            "start": parts[0],
            "stop_entries": parts[1],
            "hard_exit": parts[2]
        }
    except Exception:
        await update.message.reply_text("❌ Invalid format. Please reply with exactly 3 times separated by commas (e.g. `20:00, 20:45, 21:00`):")
        return BT_ASK_CUSTOM_TIME
        
    message = await update.message.reply_text("Processing...", parse_mode="Markdown")
    
    class MockQuery:
        def __init__(self, msg):
            self.message = msg
        async def edit_message_text(self, *args, **kwargs):
            return await self.message.edit_text(*args, **kwargs)
            
    return await bt_ask_duration(MockQuery(message), context)


async def bt_ask_duration(query, context: ContextTypes.DEFAULT_TYPE):
    """Step 6: Ask for duration."""
    preset_name = context.user_data['bt_preset']['preset_name']
    asset = context.user_data['bt_asset']
    tf = context.user_data['bt_timeframe']
    lot = context.user_data['bt_lot_size']
    display_tf = "Batch (8 Timeframes)" if tf == "batch_native" else tf
    
    text = (
        f"🧪 **Step 7: Select Duration**\n\n"
        f"**Strategy:** {preset_name}\n"
        f"**Asset:** {asset} ({display_tf})\n"
        f"**Lot Size:** {lot}\n"
    )
    time_window = context.user_data.get('bt_time_window')
    if time_window:
        text += f"**Time Window:** {time_window['start']} - {time_window['hard_exit']} (IST)\n\n"
    else:
        text += f"**Time Window:** 24/7\n\n"
        
    text += f"Choose how much historical data to download and test:"
    
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
        "leverage": 10
    }
    if "parameters" in preset:
        strategy_params.update(preset["parameters"])
        
    # Explicitly overwrite lot_size with user's sandbox selection
    strategy_params["lot_size"] = float(context.user_data.get('bt_lot_size', 1.0))
    time_window = context.user_data.get('bt_time_window')
    if time_window:
        strategy_params["time_window"] = time_window
        
    display_tf = "Batch (8 Timeframes)" if timeframe == "batch_native" else timeframe
    loading_text = f"🧪 **Initializing Sandbox Engine...**\n\nAsset: {asset}\nTimeframe: {display_tf}\n\n⏳ Please wait..."
    
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
    
    # Parse page number
    page = 0
    if query.data.startswith("bt_history_p") and query.data != "bt_history_profit_toggle":
        try:
            page = int(query.data.replace("bt_history_p", ""))
        except ValueError:
            pass
            
    if query.data == "bt_history_profit_toggle":
        context.user_data['bt_profit_only'] = not context.user_data.get('bt_profit_only', False)
        
    profit_only = context.user_data.get('bt_profit_only', False)
            
    ITEMS_PER_PAGE = 10
    skip = page * ITEMS_PER_PAGE
    
    # Fetch exactly the requested page from the database
    results, total_count = await get_backtest_results(
        user_id, sort_by="created_at", sort_order=-1, limit=ITEMS_PER_PAGE, skip=skip,
        profit_only=profit_only
    )
    
    if not results and page == 0:
        await query.edit_message_text(
            "🗄️ No past backtest results found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return
        
    total_pages = max(1, (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    
    filter_label = " (Profit Only)" if profit_only else ""
    text = f"🗄️ **Recent Backtest Results**{filter_label} (Page {page+1}/{total_pages})\n\nSelect a result to view its full details:"
    
    keyboard = []
    for r in results:
        dt = r["created_at"].strftime('%Y-%m-%d %H:%M')
        label = f"{r.get('symbol', '?')} {r.get('timeframe', '?')} | PnL: {r.get('overall_profit_pct', 0):.1f}% | {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"bt_view_{r['_id']}")])
        
    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"bt_history_p{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"bt_history_p{page+1}"))
        
    if nav_row:
        keyboard.append(nav_row)
        
    if profit_only:
        keyboard.append([InlineKeyboardButton("✅ Showing Profit Only (tap to reset)", callback_data="bt_history_profit_toggle")])
    else:
        keyboard.append([InlineKeyboardButton("📊 Show Profit Only", callback_data="bt_history_profit_toggle")])
        
    keyboard.append([InlineKeyboardButton("🧨 Delete All History", callback_data="bt_del_all_confirm")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Backtester", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_batch_results_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the specific results from a completed batch run."""
    query = update.callback_query
    await query.answer()
    
    batch_ids = context.user_data.get('bt_batch_result_ids', [])
    if not batch_ids:
        await query.edit_message_text(
            "🗄️ No batch results found in current session.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return
        
    results = await get_backtest_results_by_ids(batch_ids)
    
    # Sort results by the standard native timeframe order
    from config.constants import SUPPORTED_NATIVE_TIMEFRAMES
    order_map = {tf: i for i, tf in enumerate(SUPPORTED_NATIVE_TIMEFRAMES)}
    results.sort(key=lambda r: order_map.get(r.get('timeframe'), 99))
    
    text = f"🗄️ **Batch Backtest Results**\n\nSelect a timeframe to view full details and charts:"
    
    keyboard = []
    for r in results:
        dt = r["created_at"].strftime('%Y-%m-%d %H:%M')
        icon = "🟢" if r.get('overall_profit_pct', 0) > 0 else "🔴"
        if r.get('overall_profit_pct', 0) == 0: icon = "⚪"
        label = f"{r.get('timeframe', '?')} | PnL: {r.get('overall_profit_pct', 0):+.1f}% {icon}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"bt_view_{r['_id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Backtester", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


def _build_dir_filter_row(result_id: str, active: str = "all") -> list:
    from telegram import InlineKeyboardButton
    buttons = [
        ("📈 Long Only", "long"),
        ("📉 Short Only", "short"),
        ("📊 All Trades", "all"),
    ]
    return [
        InlineKeyboardButton(
            f"{'✅ ' if key == active else ''}{label}",
            callback_data=f"bt_dirfilter_{result_id}_{key}"
        )
        for label, key in buttons
    ]

async def bt_view_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View details of a specific past backtest."""
    query = update.callback_query
    await query.answer()
    
    result_id = query.data.replace("bt_view_", "")
    r = await get_backtest_result_by_id(result_id)
    
    # Reset direction filter when viewing a past result
    context.user_data['bt_dir_filter'] = 'all'
    
    if not r:
        await query.edit_message_text(
            "❌ Result not found or deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="bt_history")]])
        )
        return
        
    from handlers.backtest import format_report_text
    from utils.market_utils import get_max_leverage
    
    text = format_report_text(r)
    
    max_lev = get_max_leverage(r.get('symbol', 'BTCUSD'))
    std_tiers = [1, 2, 3, 5, 10, 25, 50, 100, 200]
    valid_tiers = [t for t in std_tiers if t <= max_lev]
    if max_lev not in valid_tiers:
        valid_tiers.append(int(max_lev))
        valid_tiers.sort()
        
    btn_rows = []
    current_row = []
    current_lev = r.get('leverage', 1)
    for t in valid_tiers:
        prefix = "✅ " if t == current_lev else "🔍 "
        current_row.append(InlineKeyboardButton(f"{prefix}{t}x", callback_data=f"bt_recalc_{result_id}_{t}"))
        if len(current_row) >= 5:
            btn_rows.append(current_row)
            current_row = []
    if current_row:
        btn_rows.append(current_row)
        
    keyboard = btn_rows + [
        _build_dir_filter_row(result_id, 'all'),
        [InlineKeyboardButton("📥 Resend Chart & CSV", callback_data=f"bt_resend_{result_id}")],
        [InlineKeyboardButton("📖 Glossary & Benchmarks", callback_data="bt_glossary")],
        [InlineKeyboardButton("🗑️ Delete Record", callback_data=f"bt_del_{result_id}")],
        [InlineKeyboardButton("🔙 Back to History", callback_data="bt_history")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_recalc_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instantly recalculate and update the backtest report for a new leverage."""
    query = update.callback_query
    await query.answer("Recalculating...")
    
    # data format: bt_recalc_{result_id}_{leverage}
    parts = query.data.replace("bt_recalc_", "").split("_")
    if len(parts) != 2:
        return
        
    result_id, lev_str = parts[0], parts[1]
    new_leverage = float(lev_str)
    
    # Fetch full result (including heavy arrays)
    r = await get_backtest_result_by_id(result_id, include_arrays=True)
    if not r:
        await query.message.reply_text("❌ Result not found or deleted.")
        return
        
    trade_log = r.get("trade_log", [])
    if not trade_log:
        await query.message.reply_text("❌ No trade log data available for recalculation.")
        return
        
    dir_filter = context.user_data.get('bt_dir_filter', 'all')
    if dir_filter != 'all':
        trade_log = [t for t in trade_log if t.get("direction") == dir_filter]
        if not trade_log:
            await query.answer(f"No {dir_filter} trades found in this backtest.", show_alert=True)
            return
        
    from handlers.backtest import recalculate_metrics_with_auto_capital, format_report_text
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from utils.market_utils import get_max_leverage
    
    # Recalculate everything!
    auto_cap, peak_m, max_dd_usd, metrics, advanced = recalculate_metrics_with_auto_capital(trade_log, new_leverage)
    
    # Update result dictionary with new stats so the text formatter gets the updated values
    r.update(metrics)
    r.update(advanced)
    r["leverage"] = new_leverage
    r["initial_balance"] = auto_cap
    r["peak_margin_required"] = peak_m
    
    # Generate new text
    text = format_report_text(r)
    if dir_filter != 'all':
        text = f"⚠️ **Filtered: {dir_filter.title()} trades only ({len(trade_log)} trades)**\n\n" + text
    
    # Rebuild keyboard
    max_lev = get_max_leverage(r['symbol'])
    std_tiers = [1, 2, 3, 5, 10, 25, 50, 100, 200]
    valid_tiers = [t for t in std_tiers if t <= max_lev]
    if max_lev not in valid_tiers:
        valid_tiers.append(int(max_lev))
        valid_tiers.sort()
        
    btn_rows = []
    current_row = []
    for t in valid_tiers:
        # Checkmark the currently active leverage
        prefix = "✅ " if t == new_leverage else "🔍 "
        current_row.append(InlineKeyboardButton(f"{prefix}{t}x", callback_data=f"bt_recalc_{result_id}_{t}"))
        if len(current_row) >= 5:
            btn_rows.append(current_row)
            current_row = []
    if current_row:
        btn_rows.append(current_row)
        
    keyboard = btn_rows + [
        _build_dir_filter_row(result_id, dir_filter),
        [InlineKeyboardButton("📖 Glossary & Benchmarks", callback_data="bt_glossary")],
        [InlineKeyboardButton("🔄 Backtest Another Strategy", callback_data="bt_start_fsm")],
        [InlineKeyboardButton("🔙 Back to Backtest Menu", callback_data="menu_backtest")]
    ]
    
    try:
        await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        # If it's a text message without caption
        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e2:
            pass


async def bt_dirfilter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filter trade log by direction and recalculate metrics."""
    query = update.callback_query
    await query.answer("Filtering...")
    
    # data format: bt_dirfilter_{result_id}_{direction}
    parts = query.data.replace("bt_dirfilter_", "").split("_")
    if len(parts) != 2:
        return
        
    result_id, direction = parts[0], parts[1]
    context.user_data['bt_dir_filter'] = direction
    
    # Fetch full result (including heavy arrays)
    r = await get_backtest_result_by_id(result_id, include_arrays=True)
    if not r:
        await query.message.reply_text("❌ Result not found or deleted.")
        return
        
    trade_log = r.get("trade_log", [])
    if not trade_log:
        await query.message.reply_text("❌ No trade log data available for filtering.")
        return
        
    if direction != 'all':
        trade_log = [t for t in trade_log if t.get("direction") == direction]
        if not trade_log:
            await query.answer(f"No {direction} trades found in this backtest.", show_alert=True)
            return
            
    from handlers.backtest import recalculate_metrics_with_auto_capital, format_report_text
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from utils.market_utils import get_max_leverage
    
    # Recalculate everything with the current leverage
    current_leverage = r.get("leverage", 1)
    auto_cap, peak_m, max_dd_usd, metrics, advanced = recalculate_metrics_with_auto_capital(trade_log, current_leverage)
    
    # Update result dictionary with new stats
    r.update(metrics)
    r.update(advanced)
    r["leverage"] = current_leverage
    r["initial_balance"] = auto_cap
    r["peak_margin_required"] = peak_m
    
    # Generate new text
    text = format_report_text(r)
    if direction != 'all':
        text = f"⚠️ **Filtered: {direction.title()} trades only ({len(trade_log)} trades)**\n\n" + text
    
    # Rebuild keyboard
    max_lev = get_max_leverage(r['symbol'])
    std_tiers = [1, 2, 3, 5, 10, 25, 50, 100, 200]
    valid_tiers = [t for t in std_tiers if t <= max_lev]
    if max_lev not in valid_tiers:
        valid_tiers.append(int(max_lev))
        valid_tiers.sort()
        
    btn_rows = []
    current_row = []
    for t in valid_tiers:
        prefix = "✅ " if t == current_leverage else "🔍 "
        current_row.append(InlineKeyboardButton(f"{prefix}{t}x", callback_data=f"bt_recalc_{result_id}_{t}"))
        if len(current_row) >= 5:
            btn_rows.append(current_row)
            current_row = []
    if current_row:
        btn_rows.append(current_row)
        
    keyboard = btn_rows + [
        _build_dir_filter_row(result_id, direction),
        [InlineKeyboardButton("📖 Glossary & Benchmarks", callback_data="bt_glossary")],
        [InlineKeyboardButton("🔄 Backtest Another Strategy", callback_data="bt_start_fsm")],
        [InlineKeyboardButton("🔙 Back to Backtest Menu", callback_data="menu_backtest")]
    ]
    
    try:
        await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e2:
            pass

async def bt_resend_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend the chart and CSV files for a past backtest."""
    query = update.callback_query
    await query.answer("Generating files, please wait...")
    
    result_id = query.data.replace("bt_resend_", "")
    r = await get_backtest_result_by_id(result_id, include_arrays=True)
    
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


async def bt_del_all_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation dialog to delete all backtest history."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "⚠️ **WARNING: DELETE ALL HISTORY**\n\n"
        "You are about to permanently delete **all** of your past backtest results.\n"
        "This action cannot be undone.\n\n"
        "Are you absolutely sure?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🧨 YES, DELETE ALL", callback_data="bt_del_all_execute")],
            [InlineKeyboardButton("❌ Cancel", callback_data="bt_history")]
        ]),
        parse_mode="Markdown"
    )

async def bt_del_all_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the mass deletion of all backtest results."""
    query = update.callback_query
    await query.answer("Deleting all history...")
    user_id = str(query.from_user.id)
    
    from database.crud import delete_all_backtest_results
    deleted_count = await delete_all_backtest_results(user_id)
    
    await query.edit_message_text(
        f"✅ Successfully deleted {deleted_count} backtest results.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Backtester", callback_data="menu_backtest")]
        ])
    )

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
            BT_ASK_CUSTOM_TIMEFRAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bt_custom_timeframe_received)
            ],
            BT_SELECT_LOT_SIZE: [
                CallbackQueryHandler(bt_lot_callback, pattern="^bt_lot_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bt_lot_received)
            ],
            BT_ASK_TIME_MODE: [
                CallbackQueryHandler(bt_time_mode_callback, pattern="^bt_time_")
            ],
            BT_ASK_CUSTOM_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bt_custom_time_received)
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
        CallbackQueryHandler(bt_batch_results_menu, pattern="^bt_batch_results$"),
        CallbackQueryHandler(bt_history_menu, pattern="^bt_history"),
        CallbackQueryHandler(bt_view_result, pattern="^bt_view_"),
        CallbackQueryHandler(bt_recalc_leverage, pattern="^bt_recalc_"),
        CallbackQueryHandler(bt_dirfilter, pattern="^bt_dirfilter_"),
        CallbackQueryHandler(bt_resend_result, pattern="^bt_resend_"),
        CallbackQueryHandler(bt_del_all_confirm_callback, pattern="^bt_del_all_confirm$"),
        CallbackQueryHandler(bt_del_all_execute_callback, pattern="^bt_del_all_execute$"),
        CallbackQueryHandler(bt_del_result, pattern="^bt_del_"),
        CallbackQueryHandler(bt_stop_backtest, pattern="^bt_stop$"),
        CallbackQueryHandler(bt_glossary, pattern="^bt_glossary$"),
    ]
