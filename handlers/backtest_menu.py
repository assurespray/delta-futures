"""
Interactive Menus for the Backtester

Allows users to select an existing AlgoSetup, choose a duration,
and launch the background backtest task. Also provides history views.
"""

import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler

from database.crud import get_all_active_algo_setups, get_algo_setup_by_id, get_backtest_summary, get_backtest_results, get_backtest_result_by_id
from handlers.backtest import run_backtest_task

logger = logging.getLogger(__name__)

async def menu_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main backtest menu."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = str(update.effective_user.id)
    summary = await get_backtest_summary(user_id)
    
    text = (
        "🧪 **Advanced Backtesting Engine**\n\n"
        "Select an active Algo Setup to backtest its exact parameters against historical data.\n\n"
        f"📊 **Your History**\n"
        f"• Total Runs: `{summary['total_backtests']}`\n"
        f"• Best Profit: `{summary['best_profit_pct']:.2f}%`\n"
        f"• Max Drawdown: `{summary['worst_drawdown_pct']:.2f}%`\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("▶️ Run New Backtest", callback_data="bt_run_new")],
        [InlineKeyboardButton("🗄️ View Past Results", callback_data="bt_history")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def bt_run_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of active setups to backtest."""
    query = update.callback_query
    await query.answer()
    
    setups = await get_all_active_algo_setups()
    
    if not setups:
        await query.edit_message_text(
            "❌ No active Algo Setups found.\nPlease create a setup first before running a backtest.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")]])
        )
        return
        
    text = "🧪 **Select Setup to Backtest**\n\nChoose which active strategy configuration you want to simulate:"
    
    keyboard = []
    for s in setups:
        mode = "🎮" if s.get("is_paper_trade") else "📊"
        name = f"{mode} {s['setup_name']} ({s['asset']} {s['timeframe']})"
        keyboard.append([InlineKeyboardButton(name, callback_data=f"bt_sel_{s['_id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_backtest")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_select_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for the duration after selecting a setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("bt_sel_", "")
    setup = await get_algo_setup_by_id(setup_id)
    
    if not setup:
        await query.edit_message_text(
            "❌ Setup not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="bt_run_new")]])
        )
        return
        
    # Warn about 1m memory limits implicitly by offering safe durations
    timeframe = setup.get("timeframe", "15m")
    
    text = (
        f"🧪 **Backtest Duration**\n\n"
        f"**Setup:** {setup['setup_name']}\n"
        f"**Asset:** {setup['asset']}\n"
        f"**Timeframe:** {timeframe}\n\n"
        f"Select how far back in time to simulate:"
    )
    
    # Pass setup_id and days in the callback
    keyboard = [
        [InlineKeyboardButton("7 Days", callback_data=f"bt_start_{setup_id}_7"),
         InlineKeyboardButton("30 Days", callback_data=f"bt_start_{setup_id}_30")],
        [InlineKeyboardButton("90 Days", callback_data=f"bt_start_{setup_id}_90"),
         InlineKeyboardButton("180 Days", callback_data=f"bt_start_{setup_id}_180")]
    ]
    
    # Only allow 1+ years on higher timeframes to be safe with RAM/Time
    if timeframe not in ["1m", "3m", "5m"]:
        keyboard.append([InlineKeyboardButton("1 Year (365 Days)", callback_data=f"bt_start_{setup_id}_365")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="bt_run_new")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def bt_start_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kick off the asynchronous backtest task."""
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace("bt_start_", "").split("_")
    setup_id = data[0]
    days = int(data[1])
    
    setup = await get_algo_setup_by_id(setup_id)
    if not setup:
        await query.edit_message_text("❌ Setup not found.")
        return
        
    api_id = setup["api_id"]
    symbol = setup["asset"]
    timeframe = setup["timeframe"]
    
    # Construct Strategy Params for Engine
    strategy_params = {
        "strategy_name": setup.get("indicator", "dual_supertrend"),
        "direction": setup.get("direction", "both"),
        "lot_size": setup.get("lot_size", 1),
        "initial_balance": 10000.0,
        "leverage": setup.get("paper_leverage", 10) if setup.get("is_paper_trade") else 1
    }
    # Add all custom indicator params
    if "indicator_params" in setup:
        strategy_params.update(setup["indicator_params"])
        
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    
    # 1. Update the message to loading state
    loading_text = f"🧪 **Initializing Backtest...**\n\nAsset: {symbol}\nTimeframe: {timeframe}\nDuration: {days} days\n\n⏳ Please wait..."
    await query.edit_message_text(loading_text, parse_mode="Markdown")
    
    # 2. Fire and Forget the Background Task (avoids 60s webhook timeout)
    asyncio.create_task(
        run_backtest_task(
            chat_id=chat_id,
            message_id=query.message.message_id,
            context=context,
            user_id=user_id,
            api_id=api_id,
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            strategy_params=strategy_params
        )
    )


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
        
    # Truncated view (they already have the PDF/Chart in chat history, this is just a quick recap)
    text = (
        f"📊 **Backtest Record: {r.get('symbol', 'Unknown')} ({r.get('timeframe', 'Unknown')})**\n"
        f"Run Date: `{r.get('created_at', 'Unknown')}`\n\n"
        f"💰 **Profit:** `${r.get('overall_profit', 0):.2f}` ({r.get('overall_profit_pct', 0):.2f}%)\n"
        f"📉 **Max DD:** `${r.get('max_drawdown', 0):.2f}` ({r.get('max_drawdown_pct', 0):.2f}%)\n"
        f"🎯 **Win Rate:** `{r.get('win_pct', 0):.2f}%`\n"
        f"🔮 **R-Squared:** `{r.get('r_squared', 0):.3f}`\n\n"
        f"_Note: Scroll up in your chat history to find the original Equity Curve image and TradeLog.csv file._"
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
    """Return all callback handlers for backtesting."""
    return [
        CallbackQueryHandler(menu_backtest, pattern="^menu_backtest$"),
        CallbackQueryHandler(bt_run_new, pattern="^bt_run_new$"),
        CallbackQueryHandler(bt_select_duration, pattern="^bt_sel_"),
        CallbackQueryHandler(bt_start_task, pattern="^bt_start_"),
        CallbackQueryHandler(bt_history_menu, pattern="^bt_history$"),
        CallbackQueryHandler(bt_view_result, pattern="^bt_view_"),
        CallbackQueryHandler(bt_del_result, pattern="^bt_del_"),
    ]
