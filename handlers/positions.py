from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from database.crud import get_api_credentials_by_user_decrypted
from api.positions import display_positions_for_all_apis

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching positions...")

    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)

    if not credentials:
        keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "ℹ️ No API credentials stored.\n\n"
            "Please add API credentials first from the API Menu.",
            reply_markup=reply_markup
        )
        return

    message = await display_positions_for_all_apis(credentials)

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh_positions"),
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as ex:
        if "Message is not modified" in str(ex):
            # Optionally notify the user (or just ignore)
            pass
        else:
            raise

# Add a handler for refresh_positions
async def refresh_positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Just call the same logic as positions_callback
    await positions_callback(update, context)

# In your bot setup:
# from telegram.ext import Application
# app.add_handler(CallbackQueryHandler(positions_callback, pattern="^positions$"))
# app.add_handler(CallbackQueryHandler(refresh_positions_callback, pattern="^refresh_positions$"))
