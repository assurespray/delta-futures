import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching balances...")

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

    message = "💵 **Account Balances**\n\n"

    for cred in credentials:
        api_name = cred['api_name']
        cred_id = str(cred['_id'])
        try:
            # Decrypt credentials
            full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
            if not full_cred:
                message += f"❌ **{api_name}**: Failed to load credentials\n\n"
                continue

            # Create Delta client
            client = DeltaExchangeClient(
                api_key=full_cred['api_key'],
                api_secret=full_cred['api_secret']
            )

            # Fetch *all* wallet balances
            balances_resp = await client.get("/v2/wallet/balances")
            await client.close()
            logger.info(f"Fetched wallet balances for {api_name}: {balances_resp}")

            # This structure depends on your API. Usually it's balances_resp['result']
            balances = balances_resp['result'] if balances_resp and 'result' in balances_resp else []
            if not balances:
                message += f"❌ **{api_name}**: No wallet balances found\n\n"
                continue

            # Show all assets in balances (INR, USDT, BTC, etc.)
            message += f"✅ **{api_name}**\n"
            for asset in balances:
                sym = asset.get('asset_symbol', asset.get('symbol', ''))
                bal = asset.get('balance', 0)
                avail = asset.get('available_balance', bal)
                locked = asset.get('locked_balance', 0)
                message += (
                    f"├ {sym}: Total: {bal}, Available: {avail}, Locked: {locked}\n"
                )
            message += "\n"

        except Exception as e:
            logger.error(f"❌ Error fetching balance for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
