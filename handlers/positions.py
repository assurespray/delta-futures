from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.positions import display_positions_for_all_apis

async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching positions...")

    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    # If encrypted, decrypt here as needed to retrieve api_key/api_secret
    # If you need to decrypt, do this per credential:
    # for c in credentials: c.update(await get_api_credential_by_id(c['_id'], decrypt=True))

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
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
