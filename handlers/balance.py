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

            # Fetch all assets and get INR asset_id
            assets = await client.get_assets()
            logger.info(f"Fetched assets: {assets}")  # This logs all available assets.
            inr_id = None
            for asset in assets:
                logger.info(f"Asset: symbol={asset.get('symbol')}, id={asset.get('id')}, name={asset.get('name', '')}")
                if asset.get('symbol', '').upper() == 'INR':
                    inr_id = asset.get('id')
                    break

            if not inr_id:
                await client.close()
                message += f"❌ **{api_name}**: INR asset not found\n\n"
                continue

            # Fetch INR balance using asset_id
            inr_bal = await client.get_balances(inr_id)
            await client.close()

            # Extract relevant data
            available = inr_bal.get('available_balance', 0) if inr_bal else 0
            total = inr_bal.get('total_balance', 0) if inr_bal else 0
            locked = inr_bal.get('locked_balance', 0) if inr_bal else 0

            message += f"✅ **{api_name}**\n"
            message += f"├ Total Balance: ₹{total}\n"
            message += f"├ Available: ₹{available}\n"
            message += f"└ Locked Margin: ₹{locked}\n\n"

        except Exception as e:
            logger.error(f"❌ Error fetching balance for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
