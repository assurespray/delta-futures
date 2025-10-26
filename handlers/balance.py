"""Balance display handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient
from api.account import get_account_summary

logger = logging.getLogger(__name__)


async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display account balance for all APIs.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Fetching balances...")
    
    user_id = str(query.from_user.id)
    
    # Get stored APIs
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
            # Get decrypted credentials
            full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
            
            if not full_cred:
                message += f"❌ **{api_name}**: Failed to load credentials\n\n"
                continue
            
            # Create client
            client = DeltaExchangeClient(
                api_key=full_cred['api_key'],
                api_secret=full_cred['api_secret']
            )
            
            # Get balance
            summary = await get_account_summary(client)
            await client.close()
            
            if summary:
                message += f"✅ **{api_name}**\n"
                message += f"├ Total Balance: ${summary['total_balance']} (₹{summary['total_balance_inr']})\n"
                message += f"├ Available: ${summary['available_balance']} (₹{summary['available_balance_inr']})\n"
                message += f"└ Locked Margin: ${summary['locked_margin']} (₹{summary['locked_margin_inr']})\n\n"
            else:
                message += f"❌ **{api_name}**: Failed to fetch balance\n\n"
        
        except Exception as e:
            logger.error(f"❌ Error fetching balance for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
  
