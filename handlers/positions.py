import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

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

    message = "📈 **Open Positions**\n\n"
    total_positions = 0

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

            # Fetch all open positions from the correct endpoint!
            positions_resp = await client.get("/v2/positions/open")
            await client.close()
            logger.info(f"Fetched positions for {api_name}: {positions_resp}")

            # Typically, open positions in Delta API are in positions_resp['result']
            positions = positions_resp['result'] if positions_resp and 'result' in positions_resp else []
            if not positions:
                message += f"ℹ️ **{api_name}**: No open positions\n\n"
                continue

            message += f"✅ **{api_name}** ({len(positions)} open)\n"
            for pos in positions:
                logger.info(f"Position: {pos}")
                symbol = pos.get('symbol', '')
                side = pos.get('side', '')
                entry = pos.get('entry_price', 0)
                curr = pos.get('current_price', 0)
                size = pos.get('size', 0)
                margin = pos.get('margin', 0)
                pnl = pos.get('pnl', 0)
                pnl_pct = pos.get('pnl_percentage', '')
                pnl_inr = pos.get('pnl_inr', '')
                margin_inr = pos.get('margin_inr', '')

                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                message += (
                    f"📊 **{symbol}** - {side}\n"
                    f"├ Entry: ${entry}\n"
                    f"├ Current: ${curr}\n"
                    f"├ Size: {size} contracts\n"
                    f"├ Margin: ${margin} (₹{margin_inr})\n"
                    f"└ PnL: {pnl_emoji} ${pnl} (₹{pnl_inr}) [{pnl_pct}%]\n\n"
                )
                total_positions += 1

        except Exception as e:
            logger.error(f"❌ Error fetching positions for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"

    if total_positions == 0:
        message += "ℹ️ No open positions across all accounts.\n"

    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
