"""
Paper Trading Telegram Menu Handler.

Fully modular: delete this file and remove references in bot.py
and start.py to completely remove paper trading UI.

Features:
- Create/View/Delete paper trading setups (Individual + Screener)
- View open paper positions
- Set virtual balance to any amount
- Toggle paper mode on existing setups
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config.constants import ASSET_TYPE_TEXT, ASSET_TYPE_TEXT_SHORT
from database.crud import (
    get_api_credentials_by_user, create_algo_setup,
    get_algo_setups_by_paper_mode, get_algo_setup_by_id,
    delete_algo_setup, update_algo_setup,
    get_paper_balance, reset_paper_balance, get_open_trade_by_setup,
    get_open_paper_positions,
    create_screener_setup, get_screener_setups_by_paper_mode,
    get_screener_setup_by_id, update_screener_setup,
    delete_screener_setup,
    get_strategy_presets_by_user, get_strategy_preset_by_id, ensure_default_presets,
)
from strategy.paper_trader import paper_trader
from api.delta_client import DeltaExchangeClient
from api.market_data import get_product_by_symbol
from database.crud import get_api_credential_by_id
from config.constants import PAPER_TRADE_DEFAULT_BALANCE, PAPER_TRADE_DEFAULT_LEVERAGE
from config.settings import settings

logger = logging.getLogger(__name__)


def _lev_display(lev) -> str:
    """Format leverage for display. 0 = Max (per asset)."""
    lev = int(lev or 10)
    return "Max" if lev == 0 else f"{lev}x"

def _tw_display(tw) -> str:
    """Format time_window dict for display."""
    if not tw:
        return "24/7"
    return f"{tw['start']} → {tw['stop_entries']} → {tw['hard_exit']} IST"

# Conversation states for paper INDIVIDUAL setup creation
PAPER_NAME, PAPER_DESC, PAPER_API, PAPER_DIRECTION = range(100, 104)
PAPER_TIMEFRAME, PAPER_ASSET, PAPER_LOT_SIZE, PAPER_LEVERAGE, PAPER_PROTECTION, PAPER_CONFIRM = range(104, 110)

# Conversation states for paper SCREENER setup creation
PSCR_NAME, PSCR_DESC, PSCR_API, PSCR_ASSET_TYPE = range(110, 114)
PSCR_TIMEFRAME, PSCR_DIRECTION, PSCR_LOT_SIZE, PSCR_LEVERAGE, PSCR_PROTECTION, PSCR_CONFIRM = range(114, 120)

# Time Window states for paper individual + screener
PAPER_TIME_WINDOW, PAPER_CUSTOM_TIME = 121, 122
PSCR_TIME_WINDOW, PSCR_CUSTOM_TIME = 123, 124

# Conversation state for editable virtual balance
PAPER_SET_BALANCE_AMOUNT = 120

# Conversation states for indicator preset selection
PAPER_INDICATOR = 121
PSCR_INDICATOR = 122



# ==================== Paper Individual Setup Render Functions ====================

async def render_paper_name_prompt(update, context):
    text = (
        "**Create Paper Trading Setup (Individual)**\n\n"
        "Step 1/9: Enter a name for this paper setup:\n\n"
        "Send /cancel to abort."
    )
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return PAPER_NAME

async def render_paper_desc_prompt(update, context):
    name = context.user_data.get('paper_setup_name', '?')
    text = (
        f"Name: {name}\n\n"
        "Step 2/9: Enter a description:\n\n"
        "Send /cancel to abort."
    )
    keyboard = [[
        InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_NAME"),
        InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_DESC

async def render_paper_api_selection(update, context):
    user_id = str(update.effective_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    if not credentials:
        text = "You need at least one API credential for price data.\nGo to API Menu to add one first."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return ConversationHandler.END

    text = "Step 3/9: Select API credential (for price data):"
    keyboard = []
    for cred in credentials:
        keyboard.append([InlineKeyboardButton(cred['api_name'], callback_data=f"paper_api_{cred['_id']}")])
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_DESC"),
        InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_API

async def render_paper_indicator_selection(update, context):
    user_id = str(update.effective_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)

    api_name = context.user_data.get('paper_api_name', '?')
    text = f"API: {api_name}\n\nStep 4/10: Select Indicator Strategy:"
    keyboard = []
    for preset in presets:
        pid = str(preset['_id'])
        name = preset.get('preset_name', 'Strategy')
        keyboard.append([InlineKeyboardButton(name, callback_data=f"paper_ind_{pid}")])
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_API"),
        InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_INDICATOR

async def render_paper_direction_selection(update, context):
    preset_name = context.user_data.get('paper_preset_name', '?')
    text = f"Indicator: {preset_name}\n\nStep 5/10: Select trading direction:"
    keyboard = [
        [InlineKeyboardButton("Both (Long & Short)", callback_data="paper_dir_both")],
        [InlineKeyboardButton("Long Only", callback_data="paper_dir_long_only")],
        [InlineKeyboardButton("Short Only", callback_data="paper_dir_short_only")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_INDICATOR"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_DIRECTION

async def render_paper_timeframe_selection(update, context):
    direction = context.user_data.get('paper_direction', '?')
    text = f"Direction: {direction.replace('_', ' ').title()}\n\nStep 6/10: Select timeframe:"
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="paper_tf_1m"),
            InlineKeyboardButton("3m", callback_data="paper_tf_3m"),
            InlineKeyboardButton("5m", callback_data="paper_tf_5m")
        ],
        [
            InlineKeyboardButton("15m", callback_data="paper_tf_15m"),
            InlineKeyboardButton("30m", callback_data="paper_tf_30m"),
            InlineKeyboardButton("1h", callback_data="paper_tf_1h")
        ],
        [
            InlineKeyboardButton("4h", callback_data="paper_tf_4h"),
            InlineKeyboardButton("1d", callback_data="paper_tf_1d")
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_DIRECTION"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_TIMEFRAME

async def render_paper_asset_prompt(update, context):
    timeframe = context.user_data.get('paper_timeframe', '?')
    text = (
        f"Timeframe: {timeframe}\n\n"
        "Step 7/10: Enter Asset Symbol (e.g., BTCUSD, ETHUSD):\n\n"
        "Send /cancel to abort."
    )
    keyboard = [[
        InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_TIMEFRAME"),
        InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_ASSET

async def render_paper_lot_size_prompt(update, context):
    asset = context.user_data.get('paper_asset', '?')
    text = (
        f"Asset: {asset}\n\n"
        "Step 8/10: Enter Lot Size (number of contracts):\n"
        "*(Type a custom number or select an option below)*\n\n"
        "Send /cancel to abort."
    )
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="paper_lot_1"),
            InlineKeyboardButton("2", callback_data="paper_lot_2"),
            InlineKeyboardButton("5", callback_data="paper_lot_5"),
            InlineKeyboardButton("10", callback_data="paper_lot_10")
        ],
        [
            InlineKeyboardButton("15", callback_data="paper_lot_15"),
            InlineKeyboardButton("20", callback_data="paper_lot_20"),
            InlineKeyboardButton("25", callback_data="paper_lot_25"),
            InlineKeyboardButton("50", callback_data="paper_lot_50")
        ],
        [
            InlineKeyboardButton("100", callback_data="paper_lot_100"),
            InlineKeyboardButton("200", callback_data="paper_lot_200"),
            InlineKeyboardButton("500", callback_data="paper_lot_500"),
            InlineKeyboardButton("1000", callback_data="paper_lot_1000")
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_ASSET"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_LOT_SIZE

async def render_paper_leverage_selection(update, context):
    lot_size = context.user_data.get('paper_lot_size', '?')
    text = (
        f"Lot Size: {lot_size}\n\n"
        "Step 9/10: Select Leverage:\n"
        "(Max = highest allowed by the exchange for this asset)"
    )
    keyboard = [
        [
            InlineKeyboardButton("5x", callback_data="paper_lev_5"),
            InlineKeyboardButton("10x", callback_data="paper_lev_10"),
            InlineKeyboardButton("25x", callback_data="paper_lev_25"),
        ],
        [
            InlineKeyboardButton("50x", callback_data="paper_lev_50"),
            InlineKeyboardButton("100x", callback_data="paper_lev_100"),
            InlineKeyboardButton("Max", callback_data="paper_lev_0"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_LOT_SIZE"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_LEVERAGE

async def render_paper_protection_selection(update, context):
    leverage = context.user_data.get('paper_leverage', 10)
    lev_display = "Max (per asset)" if leverage == 0 else f"{leverage}x"
    text = (
        f"Leverage: {lev_display}\n\n"
        "Step 10/11: Additional Protection (Stop-Loss)?"
    )
    keyboard = [
        [InlineKeyboardButton("Yes (Enable SL)", callback_data="paper_prot_yes")],
        [InlineKeyboardButton("No (Disable SL)", callback_data="paper_prot_no")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_LEVERAGE"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_PROTECTION

async def render_paper_confirm(update, context):
    ud = context.user_data
    protection = ud.get('paper_protection', False)
    tw = ud.get('paper_time_window')
    if tw:
        tw_display = f"{tw['start']} → {tw['stop_entries']} → {tw['hard_exit']} IST"
    else:
        tw_display = "24/7 (No Restriction)"
    text = (
        "**Paper Setup Summary**\n\n"
        f"**Name:** {ud.get('paper_setup_name', '?')}\n"
        f"**Description:** {ud.get('paper_description', '?')}\n"
        f"**API:** {ud.get('paper_api_name', '?')}\n"
        f"**Indicator:** {ud.get('paper_preset_name', ud.get('paper_indicator', 'Unknown'))}\n"
        f"**Direction:** {ud.get('paper_direction', '').replace('_', ' ').title()}\n"
        f"**Timeframe:** {ud.get('paper_timeframe', '?')}\n"
        f"**Asset:** {ud.get('paper_asset', '?')}\n"
        f"**Lot Size:** {ud.get('paper_lot_size', '?')}\n"
        f"**Leverage:** {_lev_display(ud.get('paper_leverage', 10))}\n"
        f"**Stop-Loss:** {'Enabled' if protection else 'Disabled'}\n"
        f"**Time Window:** {tw_display}\n"
        f"**Mode:** PAPER TRADE (Virtual)\n\n"
        "Confirm to save and activate?"
    )
    keyboard = [
        [InlineKeyboardButton("Confirm and Activate", callback_data="paper_confirm_yes")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_TIME_WINDOW"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return PAPER_CONFIRM



# ==================== CREATE INDIVIDUAL PAPER SETUP (CONVERSATION) ====================

async def paper_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    return await render_paper_name_prompt(update, context)

async def paper_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("❌ Name must be at least 3 characters. Try again:")
        return PAPER_NAME
    context.user_data['paper_setup_name'] = name
    return await render_paper_desc_prompt(update, context)

async def paper_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    context.user_data['paper_description'] = desc
    return await render_paper_api_selection(update, context)

async def paper_api_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    api_id = query.data.replace("paper_api_", "")
    cred = await get_api_credential_by_id(api_id, decrypt=False)
    if not cred:
        await query.edit_message_text("❌ API credential not found. Try again.")
        return ConversationHandler.END
    context.user_data['paper_api_id'] = api_id
    context.user_data['paper_api_name'] = cred['api_name']
    return await render_paper_indicator_selection(update, context)

async def paper_indicator_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    preset_id = query.data.replace("paper_ind_", "")
    preset = await get_strategy_preset_by_id(preset_id)
    if not preset:
        await query.edit_message_text("❌ Preset not found. Use /start to return.")
        return ConversationHandler.END
    context.user_data['paper_indicator'] = preset['strategy_type']
    context.user_data['paper_preset_id'] = preset_id
    context.user_data['paper_indicator_params'] = preset.get('parameters', {})
    context.user_data['paper_preset_name'] = preset.get('preset_name', 'Unknown')
    return await render_paper_direction_selection(update, context)

async def paper_direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    direction = query.data.replace("paper_dir_", "")
    context.user_data['paper_direction'] = direction
    return await render_paper_timeframe_selection(update, context)

async def paper_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    timeframe = query.data.replace("paper_tf_", "")
    context.user_data['paper_timeframe'] = timeframe
    return await render_paper_asset_prompt(update, context)

async def paper_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asset = update.message.text.strip().upper()
    if len(asset) < 3:
        await update.message.reply_text("❌ Invalid asset symbol. Try again:")
        return PAPER_ASSET
    context.user_data['paper_asset'] = asset
    return await render_paper_lot_size_prompt(update, context)

async def paper_lot_size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lot_size = int(query.data.replace("paper_lot_", ""))
    context.user_data['paper_lot_size'] = lot_size
    return await render_paper_leverage_selection(update, context)

async def paper_lot_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lot_size = int(update.message.text.strip())
        if lot_size < 1:
            await update.message.reply_text("❌ Lot size must be at least 1. Try again:")
            return PAPER_LOT_SIZE
        context.user_data['paper_lot_size'] = lot_size
        return await render_paper_leverage_selection(update, context)
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Enter a valid lot size:")
        return PAPER_LOT_SIZE

async def paper_leverage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    leverage = int(query.data.replace("paper_lev_", ""))
    context.user_data['paper_leverage'] = leverage
    return await render_paper_protection_selection(update, context)

async def paper_protection_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    protection = query.data == "paper_prot_yes"
    context.user_data['paper_protection'] = protection
    return await render_paper_time_window_selection(update, context)

async def render_paper_time_window_selection(update, context):
    protection = context.user_data.get('paper_protection', False)
    text = (
        f"Protection: {'Enabled' if protection else 'Disabled'}\n\n"
        f"Step 11/11: Time Window\n\n"
        f"Run 24/7 or restrict to a specific IST time window?\n\n"
        f"A time window controls:\n"
        f"• When new entries are allowed\n"
        f"• When entries stop (cool-down)\n"
        f"• When open positions are force-closed (hard exit)"
    )
    keyboard = [
        [InlineKeyboardButton("🌍 Run 24/7", callback_data="paper_tw_247")],
        [InlineKeyboardButton("🕒 Custom Time Window (IST)", callback_data="paper_tw_custom")],
        [
            InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_PROTECTION"),
            InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return PAPER_TIME_WINDOW

async def paper_time_window_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 24/7 vs custom time window selection for paper setup."""
    query = update.callback_query
    await query.answer()
    mode = query.data.replace("paper_tw_", "")
    if mode == "247":
        context.user_data['paper_time_window'] = None
        return await render_paper_confirm(update, context)
    else:
        text = (
            "🕒 **Custom Time Window (IST)**\n\n"
            "Reply with 3 times in `HH:MM` format, comma-separated:\n"
            "`Start, Stop Entries, Hard Exit`\n\n"
            "Example: `20:00, 20:45, 21:00`\n\n"
            "• **Start** — entries allowed from this time\n"
            "• **Stop Entries** — no new entries after this\n"
            "• **Hard Exit** — force-close any open position"
        )
        keyboard = [
            [
                InlineKeyboardButton("🔙 Back", callback_data="paper_back_to_PAPER_TIME_WINDOW"),
                InlineKeyboardButton("❌ Cancel", callback_data="paper_fsm_cancel")
            ]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return PAPER_CUSTOM_TIME

async def paper_custom_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse custom time window input for paper setup."""
    val = update.message.text.strip()
    try:
        parts = [p.strip() for p in val.split(",")]
        if len(parts) != 3:
            raise ValueError("Need exactly 3 times")
        from utils.time_utils import parse_time
        parse_time(parts[0])
        parse_time(parts[1])
        parse_time(parts[2])
        context.user_data['paper_time_window'] = {
            "start": parts[0],
            "stop_entries": parts[1],
            "hard_exit": parts[2]
        }
    except Exception:
        await update.message.reply_text(
            "❌ Invalid format. Reply with exactly 3 times separated by commas.\n\n"
            "Example: `20:00, 20:45, 21:00`",
            parse_mode="Markdown"
        )
        return PAPER_CUSTOM_TIME
    return await render_paper_confirm(update, context)

async def paper_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the paper setup to database."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "paper_confirm_no":
        await query.edit_message_text("Paper setup creation cancelled.")
        return ConversationHandler.END
    
    user_id = str(query.from_user.id)
    ud = context.user_data
    
    try:
        # Resolve product_id
        product_id = None
        cred = await get_api_credential_by_id(ud['paper_api_id'], decrypt=True)
        if cred:
            client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
            try:
                product = await get_product_by_symbol(client, ud['paper_asset'])
                if product:
                    product_id = product["id"]
            finally:
                await client.close()
        
        setup_data = {
            "user_id": user_id,
            "setup_name": ud['paper_setup_name'],
            "description": ud['paper_description'],
            "api_id": ud['paper_api_id'],
            "api_name": ud['paper_api_name'],
            "indicator": ud.get('paper_indicator', 'dual_supertrend'),
            "preset_id": ud.get('paper_preset_id'),
            "indicator_params": ud.get('paper_indicator_params', {}),
            "direction": ud['paper_direction'],
            "timeframe": ud['paper_timeframe'],
            "asset": ud['paper_asset'],
            "product_id": product_id,
            "lot_size": ud['paper_lot_size'],
            "additional_protection": ud['paper_protection'],
            "time_window": ud.get('paper_time_window'),
            "is_active": True,
            "is_paper_trade": True,
            "paper_leverage": ud['paper_leverage'],
        }
        
        setup_id = await create_algo_setup(setup_data)
        
        # Ensure paper balance exists
        await get_paper_balance(user_id)
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Another Setup", callback_data="paper_add_start")],
            [InlineKeyboardButton("🔙 Back to Paper Menu", callback_data="menu_paper_trading")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"**Paper Setup Created!**\n\n"
            f"**Name:** {ud['paper_setup_name']}\n"
            f"**Asset:** {ud['paper_asset']} @ {ud['paper_timeframe']}\n"
            f"**Leverage:** {_lev_display(ud['paper_leverage'])}\n"
            f"**Mode:** PAPER TRADE\n\n"
            f"The setup is now active and monitoring for signals.\n"
            f"Virtual trades will be executed automatically.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Failed to create paper setup: {e}")
        await query.edit_message_text(f"Failed to create paper setup: {str(e)[:200]}")
    
    return ConversationHandler.END



# ==================== CANCEL ====================

async def cancel_paper_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel paper setup creation."""
    await update.message.reply_text("Paper setup creation cancelled.")
    return ConversationHandler.END

# ==================== Back/Cancel Handlers ====================

async def paper_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = query.data.replace("paper_back_to_", "")
    
    if state == "PAPER_NAME":
        return await render_paper_name_prompt(update, context)
    elif state == "PAPER_DESC":
        return await render_paper_desc_prompt(update, context)
    elif state == "PAPER_API":
        return await render_paper_api_selection(update, context)
    elif state == "PAPER_INDICATOR":
        return await render_paper_indicator_selection(update, context)
    elif state == "PAPER_DIRECTION":
        return await render_paper_direction_selection(update, context)
    elif state == "PAPER_TIMEFRAME":
        return await render_paper_timeframe_selection(update, context)
    elif state == "PAPER_ASSET":
        return await render_paper_asset_prompt(update, context)
    elif state == "PAPER_LOT_SIZE":
        return await render_paper_lot_size_prompt(update, context)
    elif state == "PAPER_LEVERAGE":
        return await render_paper_leverage_selection(update, context)
    elif state == "PAPER_PROTECTION":
        return await render_paper_protection_selection(update, context)
    elif state == "PAPER_TIME_WINDOW":
        return await render_paper_time_window_selection(update, context)
    
    return ConversationHandler.END

async def paper_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await paper_trading_menu_callback(update, context)
    return ConversationHandler.END


async def pscr_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = query.data.replace("pscr_back_to_", "")
    
    if state == "PSCR_NAME":
        return await render_pscr_name_prompt(update, context)
    elif state == "PSCR_DESC":
        return await render_pscr_desc_prompt(update, context)
    elif state == "PSCR_API":
        return await render_pscr_api_selection(update, context)
    elif state == "PSCR_INDICATOR":
        return await render_pscr_indicator_selection(update, context)
    elif state == "PSCR_ASSET_TYPE":
        return await render_pscr_asset_type_selection(update, context)
    elif state == "PSCR_TIMEFRAME":
        return await render_pscr_timeframe_selection(update, context)
    elif state == "PSCR_DIRECTION":
        return await render_pscr_direction_selection(update, context)
    elif state == "PSCR_LOT_SIZE":
        return await render_pscr_lot_size_prompt(update, context)
    elif state == "PSCR_LEVERAGE":
        return await render_pscr_leverage_selection(update, context)
    elif state == "PSCR_PROTECTION":
        return await render_pscr_protection_selection(update, context)
    elif state == "PSCR_TIME_WINDOW":
        return await render_pscr_time_window_selection(update, context)
    
    return ConversationHandler.END

async def pscr_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await paper_trading_menu_callback(update, context)
    return ConversationHandler.END



def get_paper_setup_handlers():
    from telegram.ext import MessageHandler, filters, CallbackQueryHandler, CommandHandler, ConversationHandler
    from handlers.start import main_menu_callback
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(paper_add_start, pattern="^paper_add_start$")],
        states={
            PAPER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_name_received)],
            PAPER_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_desc_received)],
            PAPER_API: [CallbackQueryHandler(paper_api_selected, pattern="^paper_api_")],
            PAPER_DIRECTION: [CallbackQueryHandler(paper_direction_selected, pattern="^paper_dir_")],
            PAPER_TIMEFRAME: [CallbackQueryHandler(paper_timeframe_selected, pattern="^paper_tf_")],
            PAPER_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_asset_received)],
            PAPER_LOT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_lot_size_received), CallbackQueryHandler(paper_lot_size_callback, pattern="^paper_lot_")],
            PAPER_LEVERAGE: [CallbackQueryHandler(paper_leverage_selected, pattern="^paper_lev_")],
            PAPER_PROTECTION: [CallbackQueryHandler(paper_protection_selected, pattern="^paper_prot_")],
            PAPER_TIME_WINDOW: [CallbackQueryHandler(paper_time_window_callback, pattern="^paper_tw_")],
            PAPER_CUSTOM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_custom_time_received)],
            PAPER_CONFIRM: [CallbackQueryHandler(paper_confirmed, pattern="^paper_confirm_")],
        },
        fallbacks=[
            CallbackQueryHandler(paper_back_handler, pattern="^paper_back_to_"),
            CallbackQueryHandler(paper_cancel_handler, pattern="^paper_fsm_cancel$"),
            CommandHandler("cancel", cancel_paper_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
