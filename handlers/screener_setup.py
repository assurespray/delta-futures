"""Screener strategy setup management handler - Multi-asset trading."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_api_credentials_by_user, 
    create_screener_setup,
    get_screener_setups_by_user,
    delete_screener_setup,
    get_screener_setup_by_id,
    get_api_credential_by_id
)
from api.delta_client import DeltaExchangeClient
from utils.market_utils import get_available_assets, get_top_gainers, get_top_losers

logger = logging.getLogger(__name__)

# Conversation states
SCREENER_NAME, SCREENER_DESC, SCREENER_API, SCREENER_ASSET_TYPE = range(4)
SCREENER_TIMEFRAME, SCREENER_DIRECTION, SCREENER_LOT_SIZE, SCREENER_PROTECTION = range(4, 8)
SCREENER_CONFIRM = 8


async def screener_setups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display screener setups menu."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_user(user_id)
    
    message = "📊 **Screener Setups (Multi-Asset Trading)**\n\n"
    
    if setups:
        active_count = sum(1 for s in setups if s.get('is_active', False))
        message += f"You have {len(setups)} setup(s) ({active_count} active)\n\n"
    else:
        message += "No screener setups created yet.\n\n"
    
    message += "Select an option:"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Screener", callback_data="screener_add_start")],
        [InlineKeyboardButton("👁️ View Screeners", callback_data="screener_view_list")],
        [InlineKeyboardButton("🗑️ Delete Screener", callback_data="screener_delete_list")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def screener_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start screener setup conversation."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ **Create New Screener Setup**\n\n"
        "Step 1/8: Enter a name for this screener:\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    
    return SCREENER_NAME


async def screener_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive screener name."""
    name = update.message.text.strip()
    
    if len(name) < 3:
        await update.message.reply_text("❌ Name must be at least 3 characters. Please try again:")
        return SCREENER_NAME
    
    context.user_data['screener_name'] = name
    
    await update.message.reply_text(
        f"✅ Screener Name: {name}\n\n"
        f"Step 2/8: Enter a description:\n\n"
        f"Send /cancel to abort."
    )
    
    return SCREENER_DESC


async def screener_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive screener description."""
    description = update.message.text.strip()
    
    context.user_data['screener_description'] = description
    
    # Get user's APIs
    user_id = str(update.effective_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        await update.message.reply_text(
            "❌ No API credentials found.\n\n"
            "Please add API credentials first from the API Menu.\n\n"
            "Use /start to return to main menu."
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    message = f"✅ Description saved\n\n"
    message += f"Step 3/8: Select API account:\n"
    
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([InlineKeyboardButton(api_name, callback_data=f"screener_api_{cred_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup)
    
    return SCREENER_API


async def screener_api_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle API selection."""
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("screener_api_", "")
    context.user_data['screener_api_id'] = api_id
    
    # Get API name
    cred = await get_api_credential_by_id(api_id, decrypt=False)
    api_name = cred['api_name'] if cred else "Unknown"
    context.user_data['screener_api_name'] = api_name
    
    message = f"✅ API: {api_name}\n\n"
    message += f"Step 4/8: Select Asset Selection Type:\n"
    message += f"\n📊 How would you like to select assets to trade?"
    
    keyboard = [
        [InlineKeyboardButton("📊 Every Available Asset", callback_data="screener_atype_every")],
        [InlineKeyboardButton("📈 Top 10 Gainers Only", callback_data="screener_atype_gainers")],
        [InlineKeyboardButton("📉 Top 10 Losers Only", callback_data="screener_atype_losers")],
        [InlineKeyboardButton("📊 Top 10 Gainers + Losers", callback_data="screener_atype_mixed")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SCREENER_ASSET_TYPE


async def screener_asset_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle asset selection type."""
    query = update.callback_query
    await query.answer()
    
    asset_type = query.data.replace("screener_atype_", "")
    context.user_data['screener_asset_type'] = asset_type
    
    type_text_map = {
        "every": "Every Available Asset",
        "gainers": "Top 10 Gainers",
        "losers": "Top 10 Losers",
        "mixed": "Top 10 Gainers + Losers"
    }
    
    type_text = type_text_map.get(asset_type, asset_type)
    
    message = f"✅ Asset Type: {type_text}\n\n"
    message += f"Step 5/8: Select Timeframe:\n"
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="screener_tf_1m"),
            InlineKeyboardButton("3m", callback_data="screener_tf_3m"),  # ✅ NEW: 3m
            InlineKeyboardButton("5m", callback_data="screener_tf_5m")
        ],
        [
            InlineKeyboardButton("15m", callback_data="screener_tf_15m"),
            InlineKeyboardButton("30m", callback_data="screener_tf_30m"),
            InlineKeyboardButton("1h", callback_data="screener_tf_1h")
        ],
        [
            InlineKeyboardButton("4h", callback_data="screener_tf_4h"),
            InlineKeyboardButton("1d", callback_data="screener_tf_1d")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SCREENER_TIMEFRAME


async def screener_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timeframe selection."""
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace("screener_tf_", "")
    context.user_data['screener_timeframe'] = timeframe
    
    message = f"✅ Timeframe: {timeframe}\n\n"
    message += f"Step 6/8: Select Trading Direction:\n"
    
    keyboard = [
        [InlineKeyboardButton("↕️ Both Long and Short", callback_data="screener_dir_both")],
        [InlineKeyboardButton("📈 Long Entry Only", callback_data="screener_dir_long")],
        [InlineKeyboardButton("📉 Short Entry Only", callback_data="screener_dir_short")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SCREENER_DIRECTION


async def screener_direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direction selection."""
    query = update.callback_query
    await query.answer()
    
    direction_map = {
        "screener_dir_both": ("both", "Both Long and Short"),
        "screener_dir_long": ("long_only", "Long Entry Only"),
        "screener_dir_short": ("short_only", "Short Entry Only")
    }
    
    direction_code, direction_text = direction_map[query.data]
    context.user_data['screener_direction'] = direction_code
    
    message = f"✅ Direction: {direction_text}\n\n"
    message += f"Step 7/8: Enter Lot Size (per trade):\n\n"
    message += f"Send /cancel to abort."
    
    await query.edit_message_text(message)
    
    return SCREENER_LOT_SIZE


async def screener_lot_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive lot size."""
    try:
        lot_size = int(update.message.text.strip())
        
        if lot_size < 1:
            await update.message.reply_text("❌ Lot size must be at least 1. Please try again:")
            return SCREENER_LOT_SIZE
        
        context.user_data['screener_lot_size'] = lot_size
        
        message = f"✅ Lot Size: {lot_size}\n\n"
        message += f"Step 8/8: Additional Protection (Stop-Loss)?\n\n"
        message += f"If enabled, a stop-loss order will be placed at Sirusu price during entry."
        
        keyboard = [
            [InlineKeyboardButton("✅ Yes (Enable Protection)", callback_data="screener_prot_yes")],
            [InlineKeyboardButton("❌ No (Disable Protection)", callback_data="screener_prot_no")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
        
        return SCREENER_PROTECTION
    
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid lot size:")
        return SCREENER_LOT_SIZE


async def screener_protection_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle protection selection and show confirmation."""
    query = update.callback_query
    await query.answer()
    
    protection = query.data == "screener_prot_yes"
    context.user_data['screener_additional_protection'] = protection
    
    # Show confirmation
    user_data = context.user_data
    
    type_text_map = {
        "every": "Every Available Asset",
        "gainers": "Top 10 Gainers",
        "losers": "Top 10 Losers",
        "mixed": "Top 10 Gainers + Losers"
    }
    
    asset_type_text = type_text_map.get(user_data['screener_asset_type'], "Unknown")
    
    message = "✅ **Screener Setup Summary**\n\n"
    message += f"**Name:** {user_data['screener_name']}\n"
    message += f"**Description:** {user_data['screener_description']}\n"
    message += f"**API Account:** {user_data['screener_api_name']}\n"
    message += f"**Asset Selection:** {asset_type_text}\n"
    message += f"**Timeframe:** {user_data['screener_timeframe']}\n"
    message += f"**Direction:** {user_data['screener_direction'].replace('_', ' ').title()}\n"
    message += f"**Lot Size (per trade):** {user_data['screener_lot_size']}\n"
    message += f"**Stop-Loss Protection:** {'✅ Enabled' if protection else '❌ Disabled'}\n\n"
    message += f"Confirm to save and activate this screener?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm and Activate", callback_data="screener_confirm_yes")],
        [InlineKeyboardButton("❌ Cancel", callback_data="screener_confirm_no")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
    return SCREENER_CONFIRM


async def screener_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle screener confirmation and save to database."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "screener_confirm_no":
        context.user_data.clear()
        await query.edit_message_text(
            "❌ Screener setup cancelled.\n\n"
            "Use /start to return to main menu."
        )
        return ConversationHandler.END
    
    user_id = str(query.from_user.id)
    user_data = context.user_data
    
    try:
        # Create screener setup
        setup_data = {
            "user_id": user_id,
            "setup_name": user_data['screener_name'],
            "description": user_data['screener_description'],
            "api_id": user_data['screener_api_id'],
            "api_name": user_data['screener_api_name'],
            "indicator": "dual_supertrend",
            "asset_selection_type": user_data['screener_asset_type'],
            "timeframe": user_data['screener_timeframe'],
            "direction": user_data['screener_direction'],
            "lot_size": user_data['screener_lot_size'],
            "additional_protection": user_data['screener_additional_protection'],
            "is_active": True
        }
        
        setup_id = await create_screener_setup(setup_data)
        
        if not setup_id:
            raise Exception("Failed to create screener setup")
        
        type_text_map = {
            "every": "Every Available Asset",
            "gainers": "Top 10 Gainers",
            "losers": "Top 10 Losers",
            "mixed": "Top 10 Gainers + Losers"
        }
        
        asset_type_text = type_text_map.get(user_data['screener_asset_type'], "Unknown")
        
        await query.edit_message_text(
            f"✅ **Screener Setup Created Successfully!**\n\n"
            f"Setup ID: {setup_id}\n"
            f"Name: {user_data['screener_name']}\n"
            f"Asset Selection: {asset_type_text}\n"
            f"Status: 🟢 Active\n\n"
            f"The bot will now automatically:\n"
            f"• Identify {asset_type_text.lower()}\n"
            f"• Calculate indicators on {user_data['screener_timeframe']} timeframe\n"
            f"• Execute trades based on Perusu/Sirusu signals\n\n"
            f"Use /start to return to main menu.",
            parse_mode="Markdown"
        )
        
        logger.info(f"✅ Screener setup created: {setup_id} for user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Failed to create screener setup: {e}")
        await query.edit_message_text(
            f"❌ Failed to create screener setup.\n\n"
            f"Error: {str(e)[:100]}\n\n"
            f"Use /start to return to main menu."
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def screener_view_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display list of screener setups to view."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_user(user_id)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_screener_setups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ No screener setups found.\n\n"
            "Create one using 'Add New Screener'.",
            reply_markup=reply_markup
        )
        return
    
    message = "👁️ **View Screener Setups**\n\nSelect a screener to view details:\n\n"
    
    keyboard = []
    for setup in setups:
        setup_id = str(setup['_id'])
        status_emoji = "🟢" if setup.get('is_active', False) else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {setup['setup_name']}",
                callback_data=f"screener_view_{setup_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_screener_setups")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def screener_view_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display screener setup details."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("screener_view_", "")
    setup = await get_screener_setup_by_id(setup_id)
    
    if not setup:
        await query.edit_message_text(
            "❌ Screener setup not found.\n\n"
            "Use /start to return to main menu."
        )
        return
    
    status_emoji = "🟢 Active" if setup.get('is_active', False) else "🔴 Inactive"
    
    type_text_map = {
        "every": "Every Available Asset",
        "gainers": "Top 10 Gainers",
        "losers": "Top 10 Losers",
        "mixed": "Top 10 Gainers + Losers"
    }
    
    asset_type_text = type_text_map.get(setup.get('asset_selection_type'), "Unknown")
    
    message = f"📋 **Screener Setup Details**\n\n"
    message += f"**Name:** {setup['setup_name']}\n"
    message += f"**Status:** {status_emoji}\n"
    message += f"**Description:** {setup['description']}\n\n"
    message += f"**Configuration:**\n"
    message += f"├ API: {setup['api_name']}\n"
    message += f"├ Asset Selection: {asset_type_text}\n"
    message += f"├ Timeframe: {setup['timeframe']}\n"
    message += f"├ Direction: {setup['direction'].replace('_', ' ').title()}\n"
    message += f"├ Lot Size (per trade): {setup['lot_size']}\n"
    message += f"└ Stop-Loss: {'✅ Enabled' if setup.get('additional_protection') else '❌ Disabled'}\n\n"
    message += f"📊 **Active Trades:** Tracking all {asset_type_text.lower()}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔙 Back to List", callback_data="screener_view_list")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def screener_delete_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display list of screener setups to delete."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_user(user_id)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_screener_setups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ No screener setups to delete.",
            reply_markup=reply_markup
        )
        return
    
    message = "🗑️ **Delete Screener Setup**\n\nSelect a screener to delete:\n\n"
    message += "⚠️ This action cannot be undone!\n\n"
    
    keyboard = []
    for setup in setups:
        setup_id = str(setup['_id'])
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {setup['setup_name']}",
                callback_data=f"screener_delete_confirm_{setup_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_screener_setups")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def screener_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete screener setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("screener_delete_confirm_", "")
    user_id = str(query.from_user.id)
    
    # Get setup name before deleting
    setup = await get_screener_setup_by_id(setup_id)
    setup_name = setup['setup_name'] if setup else "Unknown"
    
    try:
        success = await delete_screener_setup(setup_id, user_id)
        
        if success:
            await query.edit_message_text(
                f"✅ Screener setup '{setup_name}' deleted successfully.\n\n"
                f"The bot will stop monitoring this screener.\n\n"
                f"Use /start to return to main menu."
            )
            logger.info(f"✅ Screener setup deleted: {setup_id}")
        else:
            await query.edit_message_text(
                "❌ Failed to delete screener setup.\n\n"
                "Use /start to return to main menu."
            )
    
    except Exception as e:
        logger.error(f"❌ Exception deleting screener setup: {e}")
        await query.edit_message_text(
            "❌ Error deleting screener setup.\n\n"
            "Use /start to return to main menu."
        )


async def cancel_screener_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel screener setup conversation."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Screener setup creation cancelled.\n\n"
        "Use /start to return to main menu."
    )
    return ConversationHandler.END
