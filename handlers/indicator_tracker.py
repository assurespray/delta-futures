import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_indicator_cache_by_type
from datetime import datetime

logger = logging.getLogger(__name__)

async def tracker_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu for the Live Indicator Tracker."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📊 Real - Individual", callback_data="tracker_real_algo")],
        [InlineKeyboardButton("📊 Real - Screener", callback_data="tracker_real_screener")],
        [InlineKeyboardButton("🎮 Paper - Individual", callback_data="tracker_paper_algo")],
        [InlineKeyboardButton("🎮 Paper - Screener", callback_data="tracker_paper_screener")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "**🔍 Live Indicator Tracker**\n\n"
        "View exactly which assets the bot is scanning right now, "
        "along with their live prices and SuperTrend indicators.\n\n"
        "Select a category below to view the latest calculations.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def _render_tracker_view(query, title: str, setup_type: str, is_paper: bool):
    """Helper to render the list of tracked assets."""
    await query.answer("Fetching live indicators...")
    
    caches = await get_indicator_cache_by_type(setup_type, is_paper)
    
    if not caches:
        keyboard = [[InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"ℹ️ No active scans found for **{title}** in the last hour.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    # Group by setup
    setups = {}
    for c in caches:
        setup_name = c["setup_name"]
        if setup_name not in setups:
            setups[setup_name] = []
        setups[setup_name].append(c)

    message = f"**{title} Dashboard**\n\n"

    for setup_name, assets in setups.items():
        message += f"📁 **Setup:** `{setup_name}`\n"
        for c in assets:
            asset = c["asset"]
            tf = c["timeframe"]
            price = c.get("current_price", 0.0)
            
            # Support multiple strategies via the mapped values
            p_sig_val = c.get("perusu_signal", 0)
            s_sig_val = c.get("sirusu_signal", 0)
            p_val = c.get("perusu_value", 0.0)
            s_val = c.get("sirusu_value", 0.0)
            
            p_sig = "🔵 UP" if p_sig_val == 1 else ("🔴 DOWN" if p_sig_val == -1 else "⚪ NEUTRAL")
            s_sig = "🔵 UP" if s_sig_val == 1 else ("🔴 DOWN" if s_sig_val == -1 else "⚪ NEUTRAL")
            
            # Calc age
            calc_time = c["calculated_at"]
            age_sec = (datetime.utcnow() - calc_time).total_seconds()
            age_str = f"{int(age_sec)}s ago" if age_sec < 60 else f"{int(age_sec // 60)}m ago"

            message += f"  • **{asset}** ({tf}) - ${price:.4f} `[{age_str}]`\n"
            # Generic rendering for Tracker
            if p_val == s_val and p_sig_val == s_sig_val:
                message += f"    └ Signal: {p_sig} (${p_val:.4f})\n"
            else:
                message += f"    ├ P: {p_sig} (${p_val:.4f})\n"
                message += f"    └ S: {s_sig} (${s_val:.4f})\n"
        message += "\n"

    # Truncate if too long
    if len(message) > 4000:
        message = message[:3900] + "\n... (truncated due to Telegram limits) ..."

    # Callback data matches the current view to allow refreshing
    refresh_data = f"tracker_{'paper' if is_paper else 'real'}_{setup_type}"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data=refresh_data)],
        [InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise

async def tracker_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route the specific tracker view request."""
    query = update.callback_query
    data = query.data
    
    if data == "tracker_real_algo":
        await _render_tracker_view(query, "📊 Real - Individual", "algo", False)
    elif data == "tracker_real_screener":
        await _render_tracker_view(query, "📊 Real - Screener", "screener", False)
    elif data == "tracker_paper_algo":
        await _render_tracker_view(query, "🎮 Paper - Individual", "algo", True)
    elif data == "tracker_paper_screener":
        await _render_tracker_view(query, "🎮 Paper - Screener", "screener", True)

