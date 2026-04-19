"""Algo setup management handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_api_credentials_by_user, get_algo_setups_by_user,
    create_algo_setup, delete_algo_setup, get_algo_setup_by_id,
    get_api_credential_by_id, update_algo_setup,
    get_strategy_presets_by_user, get_strategy_preset_by_id, ensure_default_presets,
    get_open_trade_by_setup
)
from api.delta_client import DeltaExchangeClient
from api.market_data import get_product_by_symbol

logger = logging.getLogger(__name__)

# Conversation states
SETUP_NAME, SETUP_DESC, SETUP_API, SETUP_INDICATOR, SETUP_DIRECTION = range(5)
SETUP_TIMEFRAME, SETUP_ASSET, SETUP_LOT_SIZE, SETUP_PROTECTION, SETUP_CONFIRM = range(5, 10)


async def algo_setups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display algo setups menu.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Get existing setups
    setups = await get_algo_setups_by_user(user_id)
    
    message = "⚙️ **Algo Setups Management**\n\n"
    
    if setups:
        active_count = sum(1 for s in setups if s.get('is_active', False))
        message += f"You have {len(setups)} setup(s) ({active_count} active)\n\n"
    else:
        message += "No algo setups created yet.\n\n"
    
    message += "Select an option:"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Setup", callback_data="algo_add_start")],
        [InlineKeyboardButton("👁️ View Setups", callback_data="algo_view_list")],
        [InlineKeyboardButton("🗑️ Delete Setup", callback_data="algo_delete_list")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def algo_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start algo setup addition conversation."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ **Create New Algo Setup**\n\n"
        "Step 1/9: Enter a name for this setup:\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    
    return SETUP_NAME


async def setup_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive setup name."""
    setup_name = update.message.text.strip()
    
    if len(setup_name) < 3:
        await update.message.reply_text("❌ Setup name must be at least 3 characters. Please try again:")
        return SETUP_NAME
    
    context.user_data['setup_name'] = setup_name
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="setup_back_name")]]
    await update.message.reply_text(
        f"✅ Setup Name: {setup_name}\n\n"
        f"Step 2/9: Enter a description for this setup:\n\n"
        f"Send /cancel to abort.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SETUP_DESC


async def setup_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive setup description."""
    description = update.message.text.strip()
    
    context.user_data['description'] = description
    
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
    
    # Show API selection
    message = f"✅ Description saved\n\n"
    message += f"Step 3/9: Select API account:\n"
    
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([InlineKeyboardButton(api_name, callback_data=f"setup_api_{cred_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup)
    
    return SETUP_API


async def setup_api_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle API selection."""
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("setup_api_", "")
    context.user_data['api_id'] = api_id
    
    # Get API name
    cred = await get_api_credential_by_id(api_id, decrypt=False)
    api_name = cred['api_name'] if cred else "Unknown"
    context.user_data['api_name'] = api_name
    
    # Show indicator presets selection
    message = f"✅ API: {api_name}\n\n"
    message += f"Step 4/9: Select Indicator Strategy:\n"
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    keyboard = []
    for preset in presets:
        pid = str(preset['_id'])
        name = preset.get('preset_name', 'Strategy')
        keyboard.append([InlineKeyboardButton(name, callback_data=f"setup_ind_{pid}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_back_api")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SETUP_INDICATOR


async def setup_indicator_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle indicator selection."""
    query = update.callback_query
    await query.answer()
    
    preset_id = query.data.replace("setup_ind_", "")
    preset = await get_strategy_preset_by_id(preset_id)
    
    if not preset:
        await query.edit_message_text("❌ Preset not found. Use /start to return.")
        context.user_data.clear()
        return ConversationHandler.END
    
    context.user_data['indicator'] = preset['strategy_type']
    context.user_data['preset_id'] = preset_id
    context.user_data['indicator_params'] = preset.get('parameters', {})
    context.user_data['preset_name'] = preset.get('preset_name', 'Unknown')
    
    # Show direction selection
    message = f"✅ Indicator: {preset.get('preset_name', 'Unknown')}\n\n"
    message += f"Step 5/9: Select Trading Direction:\n"
    
    keyboard = [
        [InlineKeyboardButton("↕️ Both Long and Short", callback_data="setup_dir_both")],
        [InlineKeyboardButton("📈 Long Entry Only", callback_data="setup_dir_long")],
        [InlineKeyboardButton("📉 Short Entry Only", callback_data="setup_dir_short")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_indicator")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SETUP_DIRECTION


async def setup_direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direction selection."""
    query = update.callback_query
    await query.answer()
    
    direction_map = {
        "setup_dir_both": ("both", "Both Long and Short"),
        "setup_dir_long": ("long_only", "Long Entry Only"),
        "setup_dir_short": ("short_only", "Short Entry Only")
    }
    
    direction_code, direction_text = direction_map[query.data]
    context.user_data['direction'] = direction_code
    
    # Show timeframe selection
    message = f"✅ Direction: {direction_text}\n\n"
    message += f"Step 6/9: Select Timeframe:\n"
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="setup_tf_1m"),
            InlineKeyboardButton("3m", callback_data="setup_tf_3m"),
            InlineKeyboardButton("5m", callback_data="setup_tf_5m")
        ],
        [
            InlineKeyboardButton("15m", callback_data="setup_tf_15m"),
            InlineKeyboardButton("30m", callback_data="setup_tf_30m"),
            InlineKeyboardButton("1h", callback_data="setup_tf_1h")
        ],
        [
            InlineKeyboardButton("4h", callback_data="setup_tf_4h"),
            InlineKeyboardButton("1d", callback_data="setup_tf_1d")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_direction")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    return SETUP_TIMEFRAME


async def setup_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timeframe selection."""
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace("setup_tf_", "")
    context.user_data['timeframe'] = timeframe
    
    message = f"✅ Timeframe: {timeframe}\n\n"
    message += f"Step 7/9: Enter Asset Symbol (e.g., BTCUSD, ETHUSD):\n\n"
    message += f"Send /cancel to abort."
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="setup_back_timeframe")]]
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return SETUP_ASSET


async def setup_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive asset symbol."""
    asset = update.message.text.strip().upper()
    
    if len(asset) < 3:
        await update.message.reply_text("❌ Invalid asset symbol. Please try again:")
        return SETUP_ASSET
    
    context.user_data['asset'] = asset
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="setup_back_asset")]]
    await update.message.reply_text(
        f"✅ Asset: {asset}\n\n"
        f"Step 8/9: Enter Lot Size (number of contracts):\n\n"
        f"Send /cancel to abort.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SETUP_LOT_SIZE


async def setup_lot_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive lot size."""
    try:
        lot_size = int(update.message.text.strip())
        
        if lot_size < 1:
            await update.message.reply_text("❌ Lot size must be at least 1. Please try again:")
            return SETUP_LOT_SIZE
        
        context.user_data['lot_size'] = lot_size
        
        # Show protection selection
        message = f"✅ Lot Size: {lot_size}\n\n"
        message += f"Step 9/9: Additional Protection (Stop-Loss)?\n\n"
        message += f"If enabled, a stop-loss order will be placed at Sirusu price during entry."
        
        keyboard = [
            [InlineKeyboardButton("✅ Yes (Enable Protection)", callback_data="setup_prot_yes")],
            [InlineKeyboardButton("❌ No (Disable Protection)", callback_data="setup_prot_no")],
            [InlineKeyboardButton("🔙 Back", callback_data="setup_back_lotsize")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
        
        return SETUP_PROTECTION
    
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a valid lot size:")
        return SETUP_LOT_SIZE


async def setup_protection_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle protection selection and show confirmation."""
    query = update.callback_query
    await query.answer()
    
    additional_protection = query.data == "setup_prot_yes"
    context.user_data['additional_protection'] = additional_protection
    
    # Show confirmation
    user_data = context.user_data
    
    message = "✅ **Algo Setup Summary**\n\n"
    message += f"**Name:** {user_data['setup_name']}\n"
    message += f"**Description:** {user_data['description']}\n"
    message += f"**API Account:** {user_data['api_name']}\n"
    message += f"**Indicator:** {user_data.get('preset_name', user_data.get('indicator', 'Unknown'))}\n"
    message += f"**Direction:** {user_data['direction'].replace('_', ' ').title()}\n"
    message += f"**Timeframe:** {user_data['timeframe']}\n"
    message += f"**Asset:** {user_data['asset']}\n"
    message += f"**Lot Size:** {user_data['lot_size']}\n"
    message += f"**Stop-Loss Protection:** {'✅ Enabled' if additional_protection else '❌ Disabled'}\n\n"
    message += f"Confirm to save and activate this setup?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm and Activate", callback_data="setup_confirm_yes")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_protection")],
        [InlineKeyboardButton("❌ Cancel", callback_data="setup_confirm_no")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
    return SETUP_CONFIRM


async def setup_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle setup confirmation and save to database."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "setup_confirm_no":
        context.user_data.clear()
        await query.edit_message_text(
            "❌ Algo setup cancelled.\n\n"
            "Use /start to return to main menu."
        )
        return ConversationHandler.END
    
    user_id = str(query.from_user.id)
    user_data = context.user_data
    
    try:
        # Validate asset symbol by checking with API
        cred = await get_api_credential_by_id(user_data['api_id'], decrypt=True)
        
        if cred:
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
            
            product = await get_product_by_symbol(client, user_data['asset'])
            await client.close()
            
            if not product:
                await query.edit_message_text(
                    f"❌ Asset symbol '{user_data['asset']}' not found on Delta Exchange.\n\n"
                    f"Please check the symbol and try again.\n\n"
                    f"Use /start to return to main menu."
                )
                context.user_data.clear()
                return ConversationHandler.END
            
            product_id = product['id']
        else:
            product_id = None
        
        # Create algo setup
        setup_data = {
            "user_id": user_id,
            "setup_name": user_data['setup_name'],
            "description": user_data['description'],
            "api_id": user_data['api_id'],
            "api_name": user_data['api_name'],
            "indicator": user_data['indicator'],
            "preset_id": user_data.get('preset_id'),
            "indicator_params": user_data.get('indicator_params', {}),
            "direction": user_data['direction'],
            "timeframe": user_data['timeframe'],
            "asset": user_data['asset'],
            "product_id": product_id,
            "lot_size": user_data['lot_size'],
            "additional_protection": user_data['additional_protection'],
            "is_active": True
        }
        
        setup_id = await create_algo_setup(setup_data)
        
        await query.edit_message_text(
            f"✅ **Algo Setup Created Successfully!**\n\n"
            f"Setup ID: {setup_id}\n"
            f"Name: {user_data['setup_name']}\n"
            f"Status: 🟢 Active\n\n"
            f"The bot will now monitor {user_data['asset']} on {user_data['timeframe']} timeframe "
            f"and execute trades automatically based on your strategy.\n\n"
            f"Use /start to return to main menu.",
            parse_mode="Markdown"
        )
        
        logger.info(f"✅ Algo setup created: {setup_id} for user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Failed to create algo setup: {e}")
        await query.edit_message_text(
            f"❌ Failed to create algo setup.\n\n"
            f"Error: {str(e)[:100]}\n\n"
            f"Use /start to return to main menu."
        )
    
    context.user_data.clear()
    return ConversationHandler.END


async def algo_view_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display list of setups to view."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_algo_setups_by_user(user_id)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_algo_setups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ No algo setups found.\n\n"
            "Create one using 'Add New Setup'.",
            reply_markup=reply_markup
        )
        return
    
    message = "👁️ **View Algo Setups**\n\nSelect a setup to view details:\n\n"
    
    keyboard = []
    for setup in setups:
        setup_id = str(setup['_id'])
        status_emoji = "🟢" if setup.get('is_active', False) else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {setup['setup_name']}",
                callback_data=f"algo_view_{setup_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_algo_setups")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def algo_view_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display algo setup details."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_view_", "")
    setup = await get_algo_setup_by_id(setup_id)
    
    if not setup:
        await query.edit_message_text(
            "❌ Algo setup not found.\n\n"
            "Use /start to return to main menu."
        )
        return
    
    status_emoji = "🟢 Active" if setup.get('is_active', False) else "🔴 Inactive"
    open_trade = await get_open_trade_by_setup(setup_id)
    position_text = open_trade["current_position"].title() if open_trade else "None"
    
    message = f"📋 **Algo Setup Details**\n\n"
    message += f"**Name:** {setup['setup_name']}\n"
    message += f"**Status:** {status_emoji}\n"
    message += f"**Description:** {setup['description']}\n\n"
    message += f"**Configuration:**\n"
    message += f"├ API: {setup['api_name']}\n"
    indicator_display = setup.get('indicator', 'unknown').replace('_', ' ').title()
    if setup.get('indicator_params'):
        params = setup['indicator_params']
        if setup['indicator'] == 'dual_supertrend':
            indicator_display = f"Dual ST (P:{params.get('perusu_atr','?')},{params.get('perusu_factor','?')} / S:{params.get('sirusu_atr','?')},{params.get('sirusu_factor','?')})"
        elif setup['indicator'] == 'single_supertrend':
            indicator_display = f"Single ST ({params.get('atr_length','?')}, {params.get('factor','?')})"
        elif setup['indicator'] == 'range_breakout_lazybear':
            indicator_display = f"Range Breakout LB (EMA:{params.get('ema_length','?')})"
    message += f"├ Indicator: {indicator_display}\n"
    message += f"├ Direction: {setup['direction'].replace('_', ' ').title()}\n"
    message += f"├ Timeframe: {setup['timeframe']}\n"
    message += f"├ Asset: {setup['asset']}\n"
    message += f"├ Lot Size: {setup['lot_size']}\n"
    message += f"└ Stop-Loss: {'✅ Enabled' if setup.get('additional_protection') else '❌ Disabled'}\n\n"
    message += f"**Current State:**\n"
    message += f"├ Position: {position_text}\n"
    
    if setup.get('last_entry_price'):
        message += f"├ Entry Price: ${setup['last_entry_price']}\n"
    
    if setup.get('last_signal_time'):
        message += f"└ Last Signal: {setup['last_signal_time'].strftime('%Y-%m-%d %H:%M UTC')}\n"
    else:
        message += f"└ Last Signal: Never\n"
    
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Setup", callback_data=f"algo_edit_{setup_id}")],
        [InlineKeyboardButton("🔙 Back to List", callback_data="algo_view_list")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# ==================== Edit Setup Handlers ====================

async def algo_edit_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show edit sub-menu for an algo setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_edit_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    setup = await get_algo_setup_by_id(setup_id)
    if not setup:
        await query.edit_message_text("❌ Setup not found. Use /start to return.")
        return
    
    message = f"✏️ **Edit Setup: {setup['setup_name']}**\n\n"
    message += "Select a field to edit:\n\n"
    message += "⚠️ Asset and API cannot be changed (unsafe mid-trade).\n"
    
    keyboard = [
        [InlineKeyboardButton("🔢 Lot Size", callback_data=f"algo_editf_lotsize_{setup_id}")],
        [InlineKeyboardButton("🔄 Direction", callback_data=f"algo_editf_direction_{setup_id}")],
        [InlineKeyboardButton("⏱️ Timeframe", callback_data=f"algo_editf_timeframe_{setup_id}")],
        [InlineKeyboardButton("🎛️ Indicator Preset", callback_data=f"algo_editf_preset_{setup_id}")],
        [InlineKeyboardButton("🛡️ Stop-Loss Protection", callback_data=f"algo_editf_protection_{setup_id}")],
        [InlineKeyboardButton("🔙 Back to Details", callback_data=f"algo_view_{setup_id}")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# ---- Edit: Direction ----

async def algo_edit_direction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show direction choices for editing."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_editf_direction_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    keyboard = [
        [InlineKeyboardButton("↕️ Both Long and Short", callback_data="algo_edset_dir_both")],
        [InlineKeyboardButton("📈 Long Entry Only", callback_data="algo_edset_dir_long_only")],
        [InlineKeyboardButton("📉 Short Entry Only", callback_data="algo_edset_dir_short_only")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"algo_edit_{setup_id}")],
    ]
    
    await query.edit_message_text(
        "🔄 **Edit Direction**\n\nSelect new trading direction:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def algo_edit_direction_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply new direction."""
    query = update.callback_query
    await query.answer()
    
    direction = query.data.replace("algo_edset_dir_", "")
    setup_id = context.user_data.get('edit_setup_id')
    
    if not setup_id:
        await query.edit_message_text("❌ Session expired. Use /start.")
        return
    
    await update_algo_setup(setup_id, {"direction": direction})
    await query.edit_message_text(
        f"✅ Direction updated to **{direction.replace('_', ' ').title()}**.\n\nReturning to details...",
        parse_mode="Markdown"
    )
    # Re-render detail view
    query.data = f"algo_view_{setup_id}"
    await algo_view_detail_callback(update, context)


# ---- Edit: Timeframe ----

async def algo_edit_timeframe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show timeframe choices for editing."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_editf_timeframe_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="algo_edset_tf_1m"),
            InlineKeyboardButton("3m", callback_data="algo_edset_tf_3m"),
            InlineKeyboardButton("5m", callback_data="algo_edset_tf_5m")
        ],
        [
            InlineKeyboardButton("15m", callback_data="algo_edset_tf_15m"),
            InlineKeyboardButton("30m", callback_data="algo_edset_tf_30m"),
            InlineKeyboardButton("1h", callback_data="algo_edset_tf_1h")
        ],
        [
            InlineKeyboardButton("4h", callback_data="algo_edset_tf_4h"),
            InlineKeyboardButton("1d", callback_data="algo_edset_tf_1d")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"algo_edit_{setup_id}")],
    ]
    
    await query.edit_message_text(
        "⏱️ **Edit Timeframe**\n\nSelect new timeframe:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def algo_edit_timeframe_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply new timeframe."""
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace("algo_edset_tf_", "")
    setup_id = context.user_data.get('edit_setup_id')
    
    if not setup_id:
        await query.edit_message_text("❌ Session expired. Use /start.")
        return
    
    await update_algo_setup(setup_id, {"timeframe": timeframe})
    query.data = f"algo_view_{setup_id}"
    await algo_view_detail_callback(update, context)


# ---- Edit: Indicator Preset ----

async def algo_edit_preset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show preset choices for editing."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_editf_preset_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    keyboard = []
    for preset in presets:
        pid = str(preset['_id'])
        name = preset.get('preset_name', 'Strategy')
        keyboard.append([InlineKeyboardButton(name, callback_data=f"algo_edset_preset_{pid}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"algo_edit_{setup_id}")])
    
    await query.edit_message_text(
        "🎛️ **Edit Indicator Preset**\n\nSelect new preset:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def algo_edit_preset_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply new indicator preset."""
    query = update.callback_query
    await query.answer()
    
    preset_id = query.data.replace("algo_edset_preset_", "")
    setup_id = context.user_data.get('edit_setup_id')
    
    if not setup_id:
        await query.edit_message_text("❌ Session expired. Use /start.")
        return
    
    preset = await get_strategy_preset_by_id(preset_id)
    if not preset:
        await query.edit_message_text("❌ Preset not found.")
        return
    
    await update_algo_setup(setup_id, {
        "indicator": preset['strategy_type'],
        "preset_id": preset_id,
        "indicator_params": preset.get('parameters', {})
    })
    
    query.data = f"algo_view_{setup_id}"
    await algo_view_detail_callback(update, context)


# ---- Edit: Protection ----

async def algo_edit_protection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show protection toggle."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_editf_protection_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes (Enable)", callback_data="algo_edset_prot_yes")],
        [InlineKeyboardButton("❌ No (Disable)", callback_data="algo_edset_prot_no")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"algo_edit_{setup_id}")],
    ]
    
    await query.edit_message_text(
        "🛡️ **Edit Stop-Loss Protection**\n\nEnable or disable?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def algo_edit_protection_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply new protection setting."""
    query = update.callback_query
    await query.answer()
    
    enabled = query.data == "algo_edset_prot_yes"
    setup_id = context.user_data.get('edit_setup_id')
    
    if not setup_id:
        await query.edit_message_text("❌ Session expired. Use /start.")
        return
    
    await update_algo_setup(setup_id, {"additional_protection": enabled})
    query.data = f"algo_view_{setup_id}"
    await algo_view_detail_callback(update, context)


# ---- Edit: Lot Size (requires text input via ConversationHandler) ----

EDIT_LOT_SIZE = 50  # Unique conversation state

async def algo_edit_lotsize_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for new lot size."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_editf_lotsize_", "")
    context.user_data['edit_setup_id'] = setup_id
    
    await query.edit_message_text(
        "🔢 **Edit Lot Size**\n\nEnter new lot size (number of contracts):\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return EDIT_LOT_SIZE


async def algo_edit_lotsize_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new lot size value."""
    try:
        lot_size = int(update.message.text.strip())
        if lot_size < 1:
            await update.message.reply_text("❌ Lot size must be at least 1. Try again:")
            return EDIT_LOT_SIZE
        
        setup_id = context.user_data.get('edit_setup_id')
        if not setup_id:
            await update.message.reply_text("❌ Session expired. Use /start.")
            return ConversationHandler.END
        
        await update_algo_setup(setup_id, {"lot_size": lot_size})
        
        await update.message.reply_text(
            f"✅ Lot size updated to **{lot_size}**.\n\nUse /start to return to main menu.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Enter a valid lot size:")
        return EDIT_LOT_SIZE


async def cancel_edit_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel edit conversation."""
    context.user_data.pop('edit_setup_id', None)
    await update.message.reply_text("❌ Edit cancelled. Use /start to return.")
    return ConversationHandler.END


# ==================== Back Button Handlers (Algo Setup Wizard) ====================

async def setup_back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 2 (Desc) to Step 1 (Name)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ **Create New Algo Setup**\n\n"
        "Step 1/9: Enter a name for this setup:\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return SETUP_NAME


async def setup_back_to_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 3 (API) to Step 2 (Desc)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"✅ Setup Name: {context.user_data.get('setup_name', '?')}\n\n"
        f"Step 2/9: Enter a description for this setup:\n\n"
        f"Send /cancel to abort."
    )
    return SETUP_DESC


async def setup_back_to_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 4 (Indicator) to Step 3 (API)."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    message = f"✅ Description saved\n\nStep 3/9: Select API account:\n"
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([InlineKeyboardButton(api_name, callback_data=f"setup_api_{cred_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_back_desc")])
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_API


async def setup_back_to_indicator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 5 (Direction) to Step 4 (Indicator)."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    message = f"✅ API: {context.user_data.get('api_name', '?')}\n\nStep 4/9: Select Indicator Strategy:\n"
    keyboard = []
    for preset in presets:
        pid = str(preset['_id'])
        name = preset.get('preset_name', 'Strategy')
        keyboard.append([InlineKeyboardButton(name, callback_data=f"setup_ind_{pid}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_back_api")])
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_INDICATOR


async def setup_back_to_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 6 (Timeframe) to Step 5 (Direction)."""
    query = update.callback_query
    await query.answer()
    
    message = f"✅ Indicator: {context.user_data.get('preset_name', '?')}\n\nStep 5/9: Select Trading Direction:\n"
    keyboard = [
        [InlineKeyboardButton("↕️ Both Long and Short", callback_data="setup_dir_both")],
        [InlineKeyboardButton("📈 Long Entry Only", callback_data="setup_dir_long")],
        [InlineKeyboardButton("📉 Short Entry Only", callback_data="setup_dir_short")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_indicator")],
    ]
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_DIRECTION


async def setup_back_to_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 7 (Asset) to Step 6 (Timeframe)."""
    query = update.callback_query
    await query.answer()
    
    direction_labels = {"both": "Both", "long_only": "Long Only", "short_only": "Short Only"}
    dir_text = direction_labels.get(context.user_data.get('direction', ''), '?')
    
    message = f"✅ Direction: {dir_text}\n\nStep 6/9: Select Timeframe:\n"
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="setup_tf_1m"),
            InlineKeyboardButton("3m", callback_data="setup_tf_3m"),
            InlineKeyboardButton("5m", callback_data="setup_tf_5m")
        ],
        [
            InlineKeyboardButton("15m", callback_data="setup_tf_15m"),
            InlineKeyboardButton("30m", callback_data="setup_tf_30m"),
            InlineKeyboardButton("1h", callback_data="setup_tf_1h")
        ],
        [
            InlineKeyboardButton("4h", callback_data="setup_tf_4h"),
            InlineKeyboardButton("1d", callback_data="setup_tf_1d")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_direction")],
    ]
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_TIMEFRAME


async def setup_back_to_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 8 (Lot Size) to Step 7 (Asset)."""
    query = update.callback_query
    await query.answer()
    
    message = f"✅ Timeframe: {context.user_data.get('timeframe', '?')}\n\n"
    message += f"Step 7/9: Enter Asset Symbol (e.g., BTCUSD, ETHUSD):\n\n"
    message += f"Send /cancel to abort."
    
    await query.edit_message_text(message)
    return SETUP_ASSET


async def setup_back_to_lotsize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 9 (Protection) to Step 8 (Lot Size)."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        f"✅ Asset: {context.user_data.get('asset', '?')}\n\n"
        f"Step 8/9: Enter Lot Size (number of contracts):\n\n"
        f"Send /cancel to abort."
    )
    return SETUP_LOT_SIZE


async def setup_back_to_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Confirm to Step 9 (Protection)."""
    query = update.callback_query
    await query.answer()
    
    message = f"✅ Lot Size: {context.user_data.get('lot_size', '?')}\n\n"
    message += f"Step 9/9: Additional Protection (Stop-Loss)?\n\n"
    message += f"If enabled, a stop-loss order will be placed at Sirusu price during entry."
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes (Enable Protection)", callback_data="setup_prot_yes")],
        [InlineKeyboardButton("❌ No (Disable Protection)", callback_data="setup_prot_no")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back_lotsize")],
    ]
    
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_PROTECTION


async def algo_delete_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display list of setups to delete."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_algo_setups_by_user(user_id)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_algo_setups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ No algo setups to delete.",
            reply_markup=reply_markup
        )
        return
    
    message = "🗑️ **Delete Algo Setup**\n\nSelect a setup to delete:\n\n"
    message += "⚠️ This action cannot be undone!\n\n"
    
    keyboard = []
    for setup in setups:
        setup_id = str(setup['_id'])
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {setup['setup_name']}",
                callback_data=f"algo_delete_confirm_{setup_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_algo_setups")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def algo_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete algo setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("algo_delete_confirm_", "")
    user_id = str(query.from_user.id)
    
    # Get setup name before deleting
    setup = await get_algo_setup_by_id(setup_id)
    setup_name = setup['setup_name'] if setup else "Unknown"
    
    try:
        success = await delete_algo_setup(setup_id, user_id)
        
        if success:
            await query.edit_message_text(
                f"✅ Algo setup '{setup_name}' deleted successfully.\n\n"
                f"The bot will stop monitoring this setup.\n\n"
                f"Use /start to return to main menu."
            )
            logger.info(f"✅ Algo setup deleted: {setup_id}")
        else:
            await query.edit_message_text(
                "❌ Failed to delete algo setup.\n\n"
                "Use /start to return to main menu."
            )
    
    except Exception as e:
        logger.error(f"❌ Exception deleting algo setup: {e}")
        await query.edit_message_text(
            "❌ Error deleting algo setup.\n\n"
            "Use /start to return to main menu."
        )


async def cancel_algo_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel algo setup conversation."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Algo setup creation cancelled.\n\n"
        "Use /start to return to main menu."
    )
    return ConversationHandler.END


async def cleanup_orphaned_stop_orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manual cleanup of orphaned stop orders.
    Usage: /cleanup_stops <product_id>
    """
    try:
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /cleanup_stops <product_id>")
            return
        
        product_id = int(context.args[0])
        
        # Get client
        client = DeltaExchangeClient(api_key="...", api_secret="...")
        
        from api.orders import cancel_all_orphaned_stop_orders
        
        cancelled = await cancel_all_orphaned_stop_orders(client, product_id)
        
        await update.message.reply_text(
            f"✅ Cleaned up {cancelled} orphaned stop orders for product {product_id}"
        )
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        
