"""Start command and main menu handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /start command and display main menu.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    user = update.effective_user
    logger.info(f"👤 User {user.id} started bot")
    
    welcome_message = (
        f"👋 Welcome to Delta Exchange Trading Bot!\n\n"
        f"🤖 Automated futures trading with SuperTrend strategies\n"
        f"📊 Real-time position monitoring\n"
        f"💰 PnL tracking in USD and INR\n\n"
        f"Select an option from the menu below:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔑 API Menu", callback_data="menu_api"),
            InlineKeyboardButton("💵 Balance", callback_data="menu_balance")
        ],
        [
            InlineKeyboardButton("📈 Positions", callback_data="menu_positions"),
            InlineKeyboardButton("📋 Orders", callback_data="menu_orders")
        ],
        [
            InlineKeyboardButton("📊 Indicators", callback_data="menu_indicators"),
            InlineKeyboardButton("⚙️ Algo Setups", callback_data="menu_algo_setups")
        ],
        [
            InlineKeyboardButton("📜 Algo Activity", callback_data="menu_algo_activity"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle back to main menu callback.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("🔑 API Menu", callback_data="menu_api"),
            InlineKeyboardButton("💵 Balance", callback_data="menu_balance")
        ],
        [
            InlineKeyboardButton("📈 Positions", callback_data="menu_positions"),
            InlineKeyboardButton("📋 Orders", callback_data="menu_orders")
        ],
        [
            InlineKeyboardButton("📊 Indicators", callback_data="menu_indicators"),
            InlineKeyboardButton("⚙️ Algo Setups", callback_data="menu_algo_setups")
        ],
        # In main_menu_callback, add this button in the keyboard:
        [InlineKeyboardButton("📊 Screener Setups", callback_data="menu_screener_setups")],
        
        [
            InlineKeyboardButton("📜 Algo Activity", callback_data="menu_algo_activity"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🏠 Main Menu\n\nSelect an option:",
        reply_markup=reply_markup
    )


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle help menu callback.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer()
    
    help_text = (
        "❓ **Delta Exchange Trading Bot Help**\n\n"
        "**🔑 API Menu**\n"
        "Store and manage your Delta Exchange API credentials.\n\n"
        "**💵 Balance**\n"
        "View account balance, available funds, and locked margin.\n\n"
        "**📈 Positions**\n"
        "Monitor open positions with real-time PnL.\n\n"
        "**📋 Orders**\n"
        "View and manage open orders.\n\n"
        "**📊 Indicators**\n"
        "Check current indicator signals (Perusu/Sirusu).\n\n"
        "**⚙️ Algo Setups**\n"
        "Create, view, and manage automated trading strategies.\n\n"
        "**📜 Algo Activity**\n"
        "View last 3 days of trading history and PnL.\n\n"
        "**Strategy Info:**\n"
        "• Perusu (20,20): Entry indicator\n"
        "• Sirusu (10,10): Exit indicator\n"
        "• Dual SuperTrend strategy for automated trading\n\n"
        "For support, contact @yoursupport"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode="Markdown")
  
