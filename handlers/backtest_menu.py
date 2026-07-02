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

from database.crud import get_strategy_presets_by_user, get_strategy_preset_by_id, get_backtest_summary, get_backtest_results, get_backtest_result_by_id, get_api_credentials_by_user
from handlers.backtest import run_backtest_task

logger = logging.getLogger(__name__)

# FSM States
BT_SELECT_PRESET = 801
BT_SELECT_ASSET = 802
BT_SELECT_TIMEFRAME = 803
BT_SELECT_DURATION = 804


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
        message = message
        async def edit_message_text(self, *args, **kwargs):
            return await message.edit_text(*args, **kwargs)
            
    return await bt_ask_timeframe(MockQuery(), context)


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
        [InlineKeyboardButton("7 Days", callback_data="bt_dur_7"),
         InlineKeyboardButton("30 Days", callback_data="bt_dur_30")],
        [InlineKeyboardButton("90 Days", callback_data="bt_dur_90"),
         InlineKeyboardButton("180 Days", callback_data="bt_dur_180")]
    ]
    
    if tf not in ["1m", "3m", "5m"]:
        keyboard.append([InlineKeyboardButton("1 Year (365 Days)", callback_data="bt_dur_365")])
        
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BT_SELECT_DURATION


async def bt_duration_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Launch the background task."""
    query = update.callback_query
    await query.answer()
    
    days = int(query.data.replace("bt_dur_", ""))
    
    preset = context.user_data['bt_preset']
    asset = context.user_data['bt_asset']
    timeframe = context.user_data['bt_timeframe']
    api_id = context.user_data['bt_api_id']
    
    # Construct Strategy Params for Engine
    strategy_params = {
        "strategy_name": preset.get("strategy_type", "dual_supertrend"),
        "direction": preset.get("parameters", {}).get("direction", "both"),
        "lot_size": 1,
        "initial_balance": 10000.0,
        "leverage": 10  # Standard paper leverage for backtesting
    }
    # Add all custom indicator params
    if "parameters" in preset:
        strategy_params.update(preset["parameters"])
        
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    
    # Update UI to loading
    loading_text = f"🧪 **Initializing Sandbox Engine...**\n\nAsset: {asset}\nTimeframe: {timeframe}\nDuration: {days} days\n\n⏳ Please wait..."
    await query.edit_message_text(loading_text, parse_mode="Markdown")
    
    # Fire and Forget Background Task
    asyncio.create_task(
        run_backtest_task(
            chat_id=chat_id,
            message_id=query.message.message_id,
            context=context,
            user_id=user_id,
            api_id=api_id,
            symbol=asset,
            timeframe=timeframe,
            days=days,
            strategy_params=strategy_params
        )
    )
    
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

    text = (
        f"📊 **Backtest Record: {r.get('symbol', 'Unknown')} ({r.get('timeframe', 'Unknown')})**\n"
        f"Run Date: `{r.get('created_at', 'Unknown')}`\n\n"
        f"⚙️ **Configuration**\n{config_str}\n\n"
        f"💰 **Profit:** `${r.get('overall_profit', 0):.2f}` ({r.get('overall_profit_pct', 0):.2f}%)\n"
        f"📉 **Max DD:** `${r.get('max_drawdown', 0):.2f}` ({r.get('max_drawdown_pct', 0):.2f}%)\n"
        f"🎯 **Win Rate:** `{r.get('win_pct', 0):.2f}%`\n"
        f"🔮 **R-Squared:** `{r.get('r_squared', 0):.3f}`\n\n"
        f"_Note: Scroll up in your chat history to find the original Equity Curve image and TradeLog file._"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Delete Record", callback_data=f"bt_del_{result_id}")],
        [InlineKeyboardButton("🔙 Back to History", callback_data="bt_history")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_del_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a specific backtest record."""
    query = update.callback_query
    await query.answer()
    
    from database.crud import delete_backtest_result
    result_id = query.data.replace("bt_del_", "")
    await delete_backtest_result(result_id)
    
    # Go back to history
    await bt_history_menu(update, context)


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
            ]
        },
        fallbacks=[
            CallbackQueryHandler(menu_backtest, pattern="^menu_backtest$"),
            CallbackQueryHandler(menu_backtest, pattern="^main_menu$")
        ],
        per_message=False
    )
    
    return [
        CallbackQueryHandler(menu_backtest, pattern="^menu_backtest$"),
        fsm_handler,
        CallbackQueryHandler(bt_history_menu, pattern="^bt_history$"),
        CallbackQueryHandler(bt_view_result, pattern="^bt_view_"),
        CallbackQueryHandler(bt_del_result, pattern="^bt_del_"),
    ]
