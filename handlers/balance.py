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
            full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
            if not full_cred:
                message += f"❌ **{api_name}**: Failed to load credentials\n\n"
                continue

            client = DeltaExchangeClient(
                api_key=full_cred['api_key'],
                api_secret=full_cred['api_secret']
            )

            balances_resp = await client.get("/v2/wallet/balances")
            await client.close()
            logger.info(f"Fetched wallet balances for {api_name}: {balances_resp}")

            balances = balances_resp['result'] if balances_resp and 'result' in balances_resp else []
            if not balances:
                message += f"❌ **{api_name}**: No wallet balances found\n\n"
                continue

            message += f"✅ **{api_name}**\n"
            for asset in balances:
                sym = asset.get('asset_symbol', asset.get('symbol', ''))
                bal = asset.get('balance', 0)
                avail = asset.get('available_balance', bal)
                locked = asset.get('locked_balance', 0)
                inr_total = asset.get('balance_inr')
                inr_avail = asset.get('available_balance_inr')
                inr_msg = f" (₹{inr_total})" if inr_total is not None else ""
                inr_avail_msg = f" (₹{inr_avail})" if inr_avail is not None else ""
                message += (
                    f"├ {sym}: Total: {bal}{inr_msg}, Available: {avail}{inr_avail_msg}, Locked: {locked}\n"
                )
            message += "\n"

        except Exception as e:
            logger.error(f"❌ Error fetching balance for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh_balance"),
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
