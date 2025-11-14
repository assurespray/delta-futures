"""Position display handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient
from api.positions import get_positions, format_positions_display

logger = logging.getLogger(__name__)


async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display open positions for all APIs.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Fetching positions...")
    
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
    
    message = "📈 **Open Positions**\n\n"
    total_positions = 0
    
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
            
            # Get positions
            positions = await get_positions(client)
            logger.info(f"Fetched positions (raw): {positions}")  # <-- ADD THIS LINE
            await client.close()
            
            if positions is not None:
                for pos in positions:
                    logger.info(f"Position info: symbol={pos.get('symbol')}, size={pos.get('size')}, side={pos.get('side')}, entry={pos.get('entry_price')}, pnl={pos.get('pnl')}")
                formatted = await format_positions_display(positions)
                logger.info(f"Formatted positions: {formatted}")  # <-- ADD THIS LINE
                
                if formatted:
                    message += f"✅ **{api_name}** ({len(formatted)} position(s))\n\n"
                    
                    for pos in formatted:
                        message += f"📊 **{pos['symbol']}** - {pos['side']}\n"
                        message += f"├ Entry: ${pos['entry_price']}\n"
                        message += f"├ Current: ${pos['current_price']}\n"
                        message += f"├ Size: {pos['size']} contracts\n"
                        message += f"├ Margin: ${pos['margin']} (₹{pos['margin_inr']})\n"
                        
                        pnl_emoji = "🟢" if pos['pnl'] >= 0 else "🔴"
                        message += f"└ PnL: {pnl_emoji} ${pos['pnl']} (₹{pos['pnl_inr']}) [{pos['pnl_percentage']}%]\n\n"
                    
                    total_positions += len(formatted)
                else:
                    message += f"ℹ️ **{api_name}**: No open positions\n\n"
            else:
                message += f"❌ **{api_name}**: Failed to fetch positions\n\n"
        
        except Exception as e:
            logger.error(f"❌ Error fetching positions for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"
    
    if total_positions == 0:
        message += "ℹ️ No open positions across all accounts.\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
  
