import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import get_custom_list, update_custom_list

logger = logging.getLogger(__name__)

RWA_ADD_TOKEN = 200

async def manage_rwa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu for managing RWA tokens."""
    query = update.callback_query
    if query:
        await query.answer()

    rwa_tokens = await get_custom_list("rwa")
    
    # Auto-migrate away from the old crypto-native RWA list to the real tokenized assets
    if rwa_tokens and "ONDOUSD" in rwa_tokens and "METAXUSD" not in rwa_tokens:
        rwa_tokens = [] # Force reset
        
    if not rwa_tokens:
        # Initialize default list
        rwa_tokens = ["PAXGUSD", "XAUTUSD", "SLVONUSD", "METAXUSD", "QQQXUSD", "SPYXUSD", "CRCLXUSD", "GOOGLXUSD", "NVDAXUSD", "COINXUSD", "TSLAXUSD", "AAPLXUSD", "AMZNXUSD"]
        await update_custom_list("rwa", rwa_tokens)

    token_list_str = ", ".join(rwa_tokens) if rwa_tokens else "None"

    message = (
        "🌐 **Manage RWA Tokens**\n\n"
        f"Currently tracked tokens ({len(rwa_tokens)}):\n"
        f"`{token_list_str}`\n\n"
        "These tokens are used as a fallback when you select 'RWA' in any screener setup."
    )

    keyboard = [
        [InlineKeyboardButton("➕ Add Token", callback_data="rwa_add_start")],
        [InlineKeyboardButton("➖ Remove Token", callback_data="rwa_remove_list")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def rwa_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start conversation to add a new RWA token."""
    query = update.callback_query
    await query.answer()

    message = (
        "➕ **Add RWA Token**\n\n"
        "Enter the exact token symbol as it appears on Delta Exchange (e.g., `ONDOUSD` or `ONDOUSDT`).\n\n"
        "Send /cancel to abort."
    )
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="rwa_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    return RWA_ADD_TOKEN

async def rwa_add_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save the new RWA token."""
    symbol = update.message.text.strip().upper()

    if len(symbol) < 3:
        await update.message.reply_text("❌ Symbol too short. Try again:")
        return RWA_ADD_TOKEN

    # Basic normalization if user forgets USD
    if not symbol.endswith("USD") and not symbol.endswith("USDT"):
        symbol += "USD"

    rwa_tokens = await get_custom_list("rwa")
    if symbol in rwa_tokens:
        await update.message.reply_text(
            f"⚠️ {symbol} is already in the list.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Manage RWA", callback_data="menu_manage_rwa")]])
        )
        return ConversationHandler.END

    rwa_tokens.append(symbol)
    await update_custom_list("rwa", rwa_tokens)

    await update.message.reply_text(
        f"✅ Added {symbol} to RWA tokens!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Manage RWA", callback_data="menu_manage_rwa")]])
    )
    return ConversationHandler.END

async def rwa_remove_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of RWA tokens to remove."""
    query = update.callback_query
    await query.answer()

    rwa_tokens = await get_custom_list("rwa")

    if not rwa_tokens:
        await query.edit_message_text(
            "No tokens to remove.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_manage_rwa")]])
        )
        return

    keyboard = []
    # Display tokens in 2 columns
    for i in range(0, len(rwa_tokens), 2):
        row = []
        for token in rwa_tokens[i:i+2]:
            row.append(InlineKeyboardButton(f"❌ {token}", callback_data=f"rwa_rem_{token}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_manage_rwa")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "➖ **Remove RWA Token**\n\nClick a token to remove it from the list:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def rwa_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the selected token from the RWA list."""
    query = update.callback_query
    await query.answer()

    token_to_remove = query.data.replace("rwa_rem_", "")
    rwa_tokens = await get_custom_list("rwa")

    if token_to_remove in rwa_tokens:
        rwa_tokens.remove(token_to_remove)
        await update_custom_list("rwa", rwa_tokens)

    # Refresh the removal list UI
    await rwa_remove_list(update, context)

async def rwa_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the add token conversation."""
    query = update.callback_query
    await query.answer()
    await manage_rwa_callback(update, context)
    return ConversationHandler.END

async def cancel_rwa_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel via text command."""
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END
