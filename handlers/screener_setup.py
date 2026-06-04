"""Screener strategy setup management handler - Multi-asset trading."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_api_credentials_by_user, 
    create_screener_setup,
    get_screener_setups_by_paper_mode,
    delete_screener_setup,
    get_screener_setup_by_id,
    get_api_credential_by_id,
    get_strategy_presets_by_user, get_strategy_preset_by_id, ensure_default_presets,
)
from api.delta_client import DeltaExchangeClient
from utils.market_utils import get_available_assets, get_top_gainers, get_top_losers
from config.constants import ASSET_TYPE_TEXT

logger = logging.getLogger(__name__)

# Conversation states
SCREENER_NAME, SCREENER_DESC, SCREENER_API = range(3)
SCREENER_INDICATOR = 3
SCREENER_ASSET_TYPE = 4
SCREENER_TIMEFRAME, SCREENER_DIRECTION, SCREENER_LOT_SIZE, SCREENER_PROTECTION = range(5, 9)
SCREENER_CONFIRM = 9


async def screener_setups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_paper_mode(user_id, is_paper=False)
    
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


# ==================== Render Functions ====================

async def render_screener_name_prompt(update, context):
    text = (
        "➕ **Create New Screener Setup**\n\n"
        "Step 1/9: Enter a name for this screener:\n\n"
        "Send /cancel to abort."
    )
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return SCREENER_NAME


async def render_screener_desc_prompt(update, context):
    name = context.user_data.get('screener_name', '?')
    text = (
        f"✅ Screener Name: {name}\n\n"
        f"Step 2/9: Enter a description:\n\n"
        f"Send /cancel to abort."
    )
    keyboard = [
        [InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_NAME"),
         InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_DESC


async def render_screener_api_selection(update, context):
    user_id = str(update.effective_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        text = (
            "❌ No API credentials found.\n\n"
            "Please add API credentials first from the API Menu.\n\n"
            "Use /start to return to main menu."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        context.user_data.clear()
        return ConversationHandler.END
    
    text = "✅ Description saved\n\nStep 3/9: Select API account:\n"
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([InlineKeyboardButton(api_name, callback_data=f"screener_api_{cred_id}")])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_DESC"),
        InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_API


async def render_screener_indicator_selection(update, context):
    user_id = str(update.effective_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    api_name = context.user_data.get('screener_api_name', '?')
    text = f"✅ API: {api_name}\n\nStep 4/9: Select Indicator Strategy:\n"
    
    keyboard = []
    for p in presets:
        pid = str(p['_id'])
        keyboard.append([InlineKeyboardButton(p['preset_name'], callback_data=f"screener_ind_{pid}")])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_API"),
        InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_INDICATOR


async def render_screener_asset_type_selection(update, context):
    preset_name = context.user_data.get('screener_preset_name', '?')
    text = (
        f"✅ Indicator: {preset_name}\n\n"
        f"Step 5/9: Select Asset Selection Type:\n\n"
        f"📊 How would you like to select assets to trade?"
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Every Available Asset", callback_data="screener_atype_every")],
        [
            InlineKeyboardButton("📈 Top Gainers", callback_data="screener_atype_gainers"),
            InlineKeyboardButton("📉 Top Losers", callback_data="screener_atype_losers")
        ],
        [
            InlineKeyboardButton("📊 Gainers + Losers", callback_data="screener_atype_mixed"),
            InlineKeyboardButton("🔊 Top Volume", callback_data="screener_atype_volume")
        ],
        [InlineKeyboardButton("🔝 Top Open Interest", callback_data="screener_atype_top_oi")],
        [
            InlineKeyboardButton("🐕 Meme", callback_data="screener_atype_meme"),
            InlineKeyboardButton("☀️ Solana", callback_data="screener_atype_solana"),
            InlineKeyboardButton("✨ New", callback_data="screener_atype_new")
        ],
        [
            InlineKeyboardButton("🤖 AI", callback_data="screener_atype_ai"),
            InlineKeyboardButton("🏦 DeFi", callback_data="screener_atype_defi"),
            InlineKeyboardButton("🎮 Gaming", callback_data="screener_atype_gaming")
        ],
        [
            InlineKeyboardButton("🔗 Layer 1", callback_data="screener_atype_layer1"),
            InlineKeyboardButton("⚡ Layer 2", callback_data="screener_atype_layer2")
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_INDICATOR"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_ASSET_TYPE


async def render_screener_timeframe_selection(update, context):
    asset_type = context.user_data.get('screener_asset_type', '?')
    type_text = ASSET_TYPE_TEXT.get(asset_type, asset_type)
    text = f"✅ Asset Type: {type_text}\n\nStep 6/9: Select Timeframe:\n"
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="screener_tf_1m"),
            InlineKeyboardButton("3m", callback_data="screener_tf_3m"),
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
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_ASSET_TYPE"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_TIMEFRAME


async def render_screener_direction_selection(update, context):
    timeframe = context.user_data.get('screener_timeframe', '?')
    text = f"✅ Timeframe: {timeframe}\n\nStep 7/9: Select Trading Direction:\n"
    
    keyboard = [
        [InlineKeyboardButton("↕️ Both Long and Short", callback_data="screener_dir_both")],
        [InlineKeyboardButton("📈 Long Entry Only", callback_data="screener_dir_long")],
        [InlineKeyboardButton("📉 Short Entry Only", callback_data="screener_dir_short")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_TIMEFRAME"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_DIRECTION


async def render_screener_lot_size_prompt(update, context):
    direction_map = {
        "both": "Both Long and Short",
        "long_only": "Long Entry Only",
        "short_only": "Short Entry Only"
    }
    dir_text = direction_map.get(context.user_data.get('screener_direction', ''), '?')
    text = (
        f"✅ Direction: {dir_text}\n\n"
        f"Step 8/9: Enter Lot Size (per trade):\n\n"
        f"Send /cancel to abort."
    )
    keyboard = [
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_DIRECTION"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_LOT_SIZE


async def render_screener_protection_selection(update, context):
    lot_size = context.user_data.get('screener_lot_size', '?')
    text = (
        f"✅ Lot Size: {lot_size}\n\n"
        f"Step 9/9: Additional Protection (Stop-Loss)?\n\n"
        f"If enabled, a stop-loss order will be placed based on the indicator strategy."
    )
    keyboard = [
        [InlineKeyboardButton("✅ Yes (Enable Protection)", callback_data="screener_prot_yes")],
        [InlineKeyboardButton("❌ No (Disable Protection)", callback_data="screener_prot_no")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_LOT_SIZE"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SCREENER_PROTECTION


async def render_screener_confirm(update, context):
    user_data = context.user_data
    protection = user_data.get('screener_additional_protection', False)
    asset_type_text = ASSET_TYPE_TEXT.get(user_data.get('screener_asset_type', ''), "Unknown")
    
    text = "✅ **Screener Setup Summary**\n\n"
    text += f"**Name:** {user_data.get('screener_name', '?')}\n"
    text += f"**Description:** {user_data.get('screener_description', '?')}\n"
    text += f"**API Account:** {user_data.get('screener_api_name', '?')}\n"
    text += f"**Indicator:** {user_data.get('screener_preset_name', user_data.get('screener_indicator', 'Unknown'))}\n"
    text += f"**Asset Selection:** {asset_type_text}\n"
    text += f"**Timeframe:** {user_data.get('screener_timeframe', '?')}\n"
    text += f"**Direction:** {user_data.get('screener_direction', '').replace('_', ' ').title()}\n"
    text += f"**Lot Size (per trade):** {user_data.get('screener_lot_size', '?')}\n"
    text += f"**Stop-Loss Protection:** {'✅ Enabled' if protection else '❌ Disabled'}\n\n"
    text += "Confirm to save and activate this screener?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm and Activate", callback_data="screener_confirm_yes")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="screener_back_to_SCREENER_PROTECTION"),
            InlineKeyboardButton("❌ Cancel", callback_data="screener_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return SCREENER_CONFIRM


# ==================== State Handlers ====================

async def screener_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    return await render_screener_name_prompt(update, context)


async def screener_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("❌ Name must be at least 3 characters. Please try again:")
        return SCREENER_NAME
    
    context.user_data['screener_name'] = name
    return await render_screener_desc_prompt(update, context)


async def screener_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    context.user_data['screener_description'] = description
    return await render_screener_api_selection(update, context)


async def screener_api_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("screener_api_", "")
    context.user_data['screener_api_id'] = api_id
    
    cred = await get_api_credential_by_id(api_id, decrypt=False)
    api_name = cred['api_name'] if cred else "Unknown"
    context.user_data['screener_api_name'] = api_name
    
    return await render_screener_indicator_selection(update, context)


async def screener_indicator_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    preset_id = query.data.replace("screener_ind_", "")
    preset = await get_strategy_preset_by_id(preset_id)
    
    if not preset:
        await query.edit_message_text("Preset not found. Use /start to return.")
        return ConversationHandler.END
    
    context.user_data['screener_indicator'] = preset['strategy_type']
    context.user_data['screener_preset_id'] = preset_id
    context.user_data['screener_indicator_params'] = preset.get('parameters', {})
    context.user_data['screener_preset_name'] = preset.get('preset_name', 'Unknown')
    
    return await render_screener_asset_type_selection(update, context)


async def screener_asset_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    asset_type = query.data.replace("screener_atype_", "")
    context.user_data['screener_asset_type'] = asset_type
    
    return await render_screener_timeframe_selection(update, context)


async def screener_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace("screener_tf_", "")
    context.user_data['screener_timeframe'] = timeframe
    
    return await render_screener_direction_selection(update, context)


async def screener_direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    direction_map = {
        "screener_dir_both": "both",
        "screener_dir_long": "long_only",
        "screener_dir_short": "short_only"
    }
    
    direction_code = direction_map[query.data]
    context.user_data['screener_direction'] = direction_code
    
    return await render_screener_lot_size_prompt(update, context)


async def screener_lot_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lot_size = int(update.message.text.strip())
        
        if lot_size < 1:
            await update.message.reply_text("❌ Lot size must be at least 1. Please try again:")
            return SCREENER_LOT_SIZE
        
        context.user_data['screener_lot_size'] = lot_size
        return await render_screener_protection_selection(update, context)
    
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid lot size:")
        return SCREENER_LOT_SIZE


async def screener_protection_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    protection = query.data == "screener_prot_yes"
    context.user_data['screener_additional_protection'] = protection
    
    return await render_screener_confirm(update, context)


async def screener_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        setup_data = {
            "user_id": user_id,
            "setup_name": user_data['screener_name'],
            "description": user_data['screener_description'],
            "api_id": user_data['screener_api_id'],
            "api_name": user_data['screener_api_name'],
            "indicator": user_data.get('screener_indicator', 'dual_supertrend'),
            "preset_id": user_data.get('screener_preset_id'),
            "indicator_params": user_data.get('screener_indicator_params', {}),
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
        
        asset_type_text = ASSET_TYPE_TEXT.get(user_data['screener_asset_type'], "Unknown")
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Another Setup", callback_data="screener_add_start")],
            [InlineKeyboardButton("🔙 Back to Screener Menu", callback_data="menu_screener_setups")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ **Screener Setup Created Successfully!**\n\n"
            f"Setup ID: {setup_id}\n"
            f"Name: {user_data['screener_name']}\n"
            f"Asset Selection: {asset_type_text}\n"
            f"Status: 🟢 Active\n\n"
            f"The bot will now automatically:\n"
            f"• Identify {asset_type_text.lower()}\n"
            f"• Calculate indicators on {user_data['screener_timeframe']} timeframe\n"
            f"• Execute trades based on indicator signals",
            reply_markup=reply_markup,
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


# ==================== Back/Cancel Handlers ====================

async def screener_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = query.data.replace("screener_back_to_", "")
    
    if state == "SCREENER_NAME":
        return await render_screener_name_prompt(update, context)
    elif state == "SCREENER_DESC":
        return await render_screener_desc_prompt(update, context)
    elif state == "SCREENER_API":
        return await render_screener_api_selection(update, context)
    elif state == "SCREENER_INDICATOR":
        return await render_screener_indicator_selection(update, context)
    elif state == "SCREENER_ASSET_TYPE":
        return await render_screener_asset_type_selection(update, context)
    elif state == "SCREENER_TIMEFRAME":
        return await render_screener_timeframe_selection(update, context)
    elif state == "SCREENER_DIRECTION":
        return await render_screener_direction_selection(update, context)
    elif state == "SCREENER_LOT_SIZE":
        return await render_screener_lot_size_prompt(update, context)
    elif state == "SCREENER_PROTECTION":
        return await render_screener_protection_selection(update, context)
    
    return ConversationHandler.END


async def screener_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await screener_setups_callback(update, context)
    return ConversationHandler.END


# ==================== View Screener Setups ====================

async def screener_view_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_paper_mode(user_id, is_paper=False)
    
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
    
    asset_type_text = ASSET_TYPE_TEXT.get(setup.get('asset_selection_type'), "Unknown")
    
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


# ==================== Delete Screener Setup ====================

async def screener_delete_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_screener_setups_by_paper_mode(user_id, is_paper=False)
    
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
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("screener_delete_confirm_", "")
    user_id = str(query.from_user.id)
    
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
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Screener setup creation cancelled.\n\n"
        "Use /start to return to main menu."
    )
    return ConversationHandler.END
