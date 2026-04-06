"""
Paper Trading Telegram Menu Handler.

Fully modular: delete this file and remove references in bot.py
and start.py to completely remove paper trading UI.

Features:
- Create/View/Delete paper trading setups
- View open paper positions
- Reset virtual balance
- Toggle paper mode on existing setups
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_api_credentials_by_user, create_algo_setup,
    get_algo_setups_by_paper_mode, get_algo_setup_by_id,
    delete_algo_setup, update_algo_setup,
    get_paper_balance, reset_paper_balance,
    get_open_paper_positions,
)
from api.delta_client import DeltaExchangeClient
from api.market_data import get_product_by_symbol
from database.crud import get_api_credential_by_id
from config.constants import PAPER_TRADE_DEFAULT_BALANCE, PAPER_TRADE_DEFAULT_LEVERAGE
from config.settings import settings

logger = logging.getLogger(__name__)

# Conversation states for paper setup creation
PAPER_NAME, PAPER_DESC, PAPER_API, PAPER_DIRECTION = range(100, 104)
PAPER_TIMEFRAME, PAPER_ASSET, PAPER_LOT_SIZE, PAPER_LEVERAGE, PAPER_PROTECTION, PAPER_CONFIRM = range(104, 110)


# ==================== MAIN PAPER TRADING MENU ====================

async def paper_trading_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display paper trading main menu."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Get paper balance
    paper_bal = await get_paper_balance(user_id)
    balance = paper_bal["balance"] if paper_bal else PAPER_TRADE_DEFAULT_BALANCE
    balance_inr = balance * settings.usd_to_inr_rate
    locked = paper_bal.get("locked_margin", 0) if paper_bal else 0
    
    # Get paper setups
    paper_setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    active_count = sum(1 for s in paper_setups if s.get("is_active", False))
    
    # Get open positions
    open_positions = await get_open_paper_positions(user_id)
    
    message = (
        "**Paper Trading Hub**\n\n"
        f"**Virtual Balance:** ${balance:.2f} ({chr(8377)}{balance_inr:.2f})\n"
        f"**Locked Margin:** ${locked:.2f}\n"
        f"**Available:** ${balance - locked:.2f}\n\n"
        f"**Setups:** {len(paper_setups)} ({active_count} active)\n"
        f"**Open Positions:** {len(open_positions)}\n\n"
        "Select an option:"
    )
    
    keyboard = [
        [InlineKeyboardButton("+ Create Paper Setup", callback_data="paper_add_start")],
        [InlineKeyboardButton("View Paper Setups", callback_data="paper_view_list")],
        [InlineKeyboardButton("Open Positions", callback_data="paper_open_positions")],
        [InlineKeyboardButton("Delete Paper Setup", callback_data="paper_delete_list")],
        [InlineKeyboardButton("Reset Virtual Balance", callback_data="paper_reset_balance")],
        [InlineKeyboardButton("Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# ==================== CREATE PAPER SETUP (CONVERSATION) ====================

async def paper_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start paper setup creation."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "**Create Paper Trading Setup**\n\n"
        "Step 1/9: Enter a name for this paper setup:\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return PAPER_NAME


async def paper_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive paper setup name."""
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("Name must be at least 3 characters. Try again:")
        return PAPER_NAME
    
    context.user_data['paper_setup_name'] = name
    await update.message.reply_text(
        f"Name: {name}\n\n"
        "Step 2/9: Enter a description:\n\n"
        "Send /cancel to abort."
    )
    return PAPER_DESC


async def paper_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive paper setup description."""
    desc = update.message.text.strip()
    context.user_data['paper_description'] = desc
    
    user_id = str(update.effective_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        await update.message.reply_text(
            "You need at least one API credential for price data.\n"
            "Go to API Menu to add one first."
        )
        return ConversationHandler.END
    
    keyboard = []
    for cred in credentials:
        keyboard.append([InlineKeyboardButton(
            cred['api_name'],
            callback_data=f"paper_api_{cred['_id']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Step 3/9: Select API credential (for price data):",
        reply_markup=reply_markup
    )
    return PAPER_API


async def paper_api_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle API selection."""
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("paper_api_", "")
    cred = await get_api_credential_by_id(api_id, decrypt=False)
    
    if not cred:
        await query.edit_message_text("API credential not found. Try again.")
        return ConversationHandler.END
    
    context.user_data['paper_api_id'] = api_id
    context.user_data['paper_api_name'] = cred['api_name']
    
    keyboard = [
        [InlineKeyboardButton("Both (Long & Short)", callback_data="paper_dir_both")],
        [InlineKeyboardButton("Long Only", callback_data="paper_dir_long_only")],
        [InlineKeyboardButton("Short Only", callback_data="paper_dir_short_only")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"API: {cred['api_name']}\n\n"
        "Step 4/9: Select trading direction:",
        reply_markup=reply_markup
    )
    return PAPER_DIRECTION


async def paper_direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direction selection."""
    query = update.callback_query
    await query.answer()
    
    direction = query.data.replace("paper_dir_", "")
    context.user_data['paper_direction'] = direction
    
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
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Direction: {direction.replace('_', ' ').title()}\n\n"
        "Step 5/9: Select timeframe:",
        reply_markup=reply_markup
    )
    return PAPER_TIMEFRAME


async def paper_timeframe_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timeframe selection."""
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace("paper_tf_", "")
    context.user_data['paper_timeframe'] = timeframe
    
    await query.edit_message_text(
        f"Timeframe: {timeframe}\n\n"
        "Step 6/9: Enter Asset Symbol (e.g., BTCUSD, ETHUSD):\n\n"
        "Send /cancel to abort."
    )
    return PAPER_ASSET


async def paper_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive asset symbol."""
    asset = update.message.text.strip().upper()
    if len(asset) < 3:
        await update.message.reply_text("Invalid asset symbol. Try again:")
        return PAPER_ASSET
    
    context.user_data['paper_asset'] = asset
    
    await update.message.reply_text(
        f"Asset: {asset}\n\n"
        "Step 7/9: Enter Lot Size (number of contracts):\n\n"
        "Send /cancel to abort."
    )
    return PAPER_LOT_SIZE


async def paper_lot_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive lot size."""
    try:
        lot_size = int(update.message.text.strip())
        if lot_size < 1:
            await update.message.reply_text("Lot size must be at least 1. Try again:")
            return PAPER_LOT_SIZE
        
        context.user_data['paper_lot_size'] = lot_size
        
        keyboard = [
            [
                InlineKeyboardButton("5x", callback_data="paper_lev_5"),
                InlineKeyboardButton("10x", callback_data="paper_lev_10"),
                InlineKeyboardButton("25x", callback_data="paper_lev_25"),
            ],
            [
                InlineKeyboardButton("50x", callback_data="paper_lev_50"),
                InlineKeyboardButton("100x", callback_data="paper_lev_100"),
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Lot Size: {lot_size}\n\n"
            "Step 8/9: Select Leverage:",
            reply_markup=reply_markup
        )
        return PAPER_LEVERAGE
        
    except ValueError:
        await update.message.reply_text("Invalid number. Enter a valid lot size:")
        return PAPER_LOT_SIZE


async def paper_leverage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle leverage selection."""
    query = update.callback_query
    await query.answer()
    
    leverage = int(query.data.replace("paper_lev_", ""))
    context.user_data['paper_leverage'] = leverage
    
    keyboard = [
        [InlineKeyboardButton("Yes (Enable SL)", callback_data="paper_prot_yes")],
        [InlineKeyboardButton("No (Disable SL)", callback_data="paper_prot_no")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Leverage: {leverage}x\n\n"
        "Step 9/9: Additional Protection (Stop-Loss)?",
        reply_markup=reply_markup
    )
    return PAPER_PROTECTION


async def paper_protection_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle protection selection and show confirmation."""
    query = update.callback_query
    await query.answer()
    
    protection = query.data == "paper_prot_yes"
    context.user_data['paper_protection'] = protection
    
    ud = context.user_data
    
    message = (
        "**Paper Setup Summary**\n\n"
        f"**Name:** {ud['paper_setup_name']}\n"
        f"**Description:** {ud['paper_description']}\n"
        f"**API:** {ud['paper_api_name']}\n"
        f"**Indicator:** Dual SuperTrend\n"
        f"**Direction:** {ud['paper_direction'].replace('_', ' ').title()}\n"
        f"**Timeframe:** {ud['paper_timeframe']}\n"
        f"**Asset:** {ud['paper_asset']}\n"
        f"**Lot Size:** {ud['paper_lot_size']}\n"
        f"**Leverage:** {ud['paper_leverage']}x\n"
        f"**Stop-Loss:** {'Enabled' if protection else 'Disabled'}\n"
        f"**Mode:** PAPER TRADE (Virtual)\n\n"
        "Confirm to save and activate?"
    )
    
    keyboard = [
        [InlineKeyboardButton("Confirm and Activate", callback_data="paper_confirm_yes")],
        [InlineKeyboardButton("Cancel", callback_data="paper_confirm_no")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    return PAPER_CONFIRM


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
            "indicator": "dual_supertrend",
            "direction": ud['paper_direction'],
            "timeframe": ud['paper_timeframe'],
            "asset": ud['paper_asset'],
            "product_id": product_id,
            "lot_size": ud['paper_lot_size'],
            "additional_protection": ud['paper_protection'],
            "is_active": True,
            "is_paper_trade": True,
            "paper_leverage": ud['paper_leverage'],
        }
        
        setup_id = await create_algo_setup(setup_data)
        
        # Ensure paper balance exists
        await get_paper_balance(user_id)
        
        await query.edit_message_text(
            f"**Paper Setup Created!**\n\n"
            f"**Name:** {ud['paper_setup_name']}\n"
            f"**Asset:** {ud['paper_asset']} @ {ud['paper_timeframe']}\n"
            f"**Leverage:** {ud['paper_leverage']}x\n"
            f"**Mode:** PAPER TRADE\n\n"
            f"The setup is now active and monitoring for signals.\n"
            f"Virtual trades will be executed automatically.",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Failed to create paper setup: {e}")
        await query.edit_message_text(f"Failed to create paper setup: {str(e)[:200]}")
    
    return ConversationHandler.END


# ==================== VIEW PAPER SETUPS ====================

async def paper_view_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View list of paper trading setups."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("No paper trading setups found.", reply_markup=reply_markup)
        return
    
    message = "**Paper Trading Setups**\n\n"
    keyboard = []
    
    for setup in setups:
        status = "Active" if setup.get("is_active") else "Inactive"
        position = setup.get("current_position", "None")
        pos_text = f" | {position.upper()}" if position else ""
        
        message += (
            f"**{setup['setup_name']}**\n"
            f"  {setup['asset']} @ {setup['timeframe']} | "
            f"{setup.get('paper_leverage', 10)}x | {status}{pos_text}\n\n"
        )
        
        keyboard.append([InlineKeyboardButton(
            f"{setup['setup_name']} - {setup['asset']}",
            callback_data=f"paper_detail_{setup['_id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_paper_trading")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def paper_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detail of a single paper setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("paper_detail_", "")
    setup = await get_algo_setup_by_id(setup_id)
    
    if not setup:
        await query.edit_message_text("Setup not found.")
        return
    
    status = "Active" if setup.get("is_active") else "Inactive"
    position = setup.get("current_position") or "None"
    entry_price = setup.get("last_entry_price")
    sl_price = setup.get("pending_sl_price")
    
    message = (
        f"**[PAPER] {setup['setup_name']}**\n\n"
        f"**Asset:** {setup['asset']}\n"
        f"**Timeframe:** {setup['timeframe']}\n"
        f"**Direction:** {setup.get('direction', 'both').replace('_', ' ').title()}\n"
        f"**Lot Size:** {setup['lot_size']}\n"
        f"**Leverage:** {setup.get('paper_leverage', 10)}x\n"
        f"**SL Protection:** {'Yes' if setup.get('additional_protection') else 'No'}\n"
        f"**Status:** {status}\n\n"
        f"**Current Position:** {position.upper()}\n"
    )
    
    if entry_price:
        message += f"**Entry Price:** ${entry_price:.5f}\n"
    if sl_price:
        message += f"**Stop-Loss:** ${sl_price:.5f}\n"
    
    # Toggle button
    if setup.get("is_active"):
        toggle_text = "Pause Setup"
        toggle_data = f"paper_toggle_{setup_id}_off"
    else:
        toggle_text = "Activate Setup"
        toggle_data = f"paper_toggle_{setup_id}_on"
    
    keyboard = [
        [InlineKeyboardButton(toggle_text, callback_data=toggle_data)],
        [InlineKeyboardButton("Back to List", callback_data="paper_view_list")],
        [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def paper_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle paper setup active/inactive."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.replace("paper_toggle_", "").rsplit("_", 1)
    setup_id = parts[0]
    action = parts[1]
    
    new_active = action == "on"
    await update_algo_setup(setup_id, {"is_active": new_active})
    
    status = "activated" if new_active else "paused"
    await query.edit_message_text(
        f"Paper setup has been {status}.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to List", callback_data="paper_view_list")]
        ])
    )


# ==================== OPEN PAPER POSITIONS ====================

async def paper_open_positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View open paper trading positions."""
    query = update.callback_query
    await query.answer("Fetching positions...")
    
    user_id = str(query.from_user.id)
    positions = await get_open_paper_positions(user_id)
    
    if not positions:
        keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "No open paper positions.",
            reply_markup=reply_markup
        )
        return
    
    message = "**Open Paper Positions**\n\n"
    
    for pos in positions:
        direction = pos.get("direction", "").upper()
        asset = pos.get("asset", "")
        entry = pos.get("entry_price", 0)
        lot = pos.get("lot_size", 0)
        entry_time = pos.get("entry_time")
        liq_price = pos.get("paper_liquidation_price")
        
        message += (
            f"**{asset}** - {direction}\n"
            f"  Entry: ${entry:.5f} | Lots: {lot}\n"
        )
        if liq_price:
            message += f"  Liquidation: ${liq_price:.5f}\n"
        if entry_time:
            message += f"  Since: {entry_time.strftime('%m/%d %H:%M')}\n"
        message += "\n"
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# ==================== DELETE PAPER SETUP ====================

async def paper_delete_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List paper setups for deletion."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    
    if not setups:
        keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("No paper setups to delete.", reply_markup=reply_markup)
        return
    
    keyboard = []
    for setup in setups:
        keyboard.append([InlineKeyboardButton(
            f"Delete: {setup['setup_name']} - {setup['asset']}",
            callback_data=f"paper_del_confirm_{setup['_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_paper_trading")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Select a paper setup to delete:",
        reply_markup=reply_markup
    )


async def paper_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete a paper setup."""
    query = update.callback_query
    await query.answer()
    
    setup_id = query.data.replace("paper_del_confirm_", "")
    user_id = str(query.from_user.id)
    
    success = await delete_algo_setup(setup_id, user_id)
    
    if success:
        await query.edit_message_text(
            "Paper setup deleted successfully.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
            ])
        )
    else:
        await query.edit_message_text(
            "Failed to delete paper setup.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
            ])
        )


# ==================== RESET VIRTUAL BALANCE ====================

async def paper_reset_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset virtual balance confirmation."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton(
            f"Yes, Reset to ${PAPER_TRADE_DEFAULT_BALANCE:.0f}",
            callback_data="paper_reset_confirm"
        )],
        [InlineKeyboardButton("Cancel", callback_data="menu_paper_trading")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "**Reset Virtual Balance?**\n\n"
        f"This will reset your paper balance to ${PAPER_TRADE_DEFAULT_BALANCE:.0f} "
        "and clear all performance stats.\n\n"
        "Open paper positions will NOT be affected.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def paper_reset_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute balance reset."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    success = await reset_paper_balance(user_id, PAPER_TRADE_DEFAULT_BALANCE)
    
    if success:
        await query.edit_message_text(
            f"Virtual balance reset to ${PAPER_TRADE_DEFAULT_BALANCE:.0f}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
            ])
        )
    else:
        await query.edit_message_text("Failed to reset balance.")


# ==================== CANCEL ====================

async def cancel_paper_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel paper setup creation."""
    await update.message.reply_text("Paper setup creation cancelled.")
    return ConversationHandler.END
