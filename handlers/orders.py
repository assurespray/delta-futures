"""Orders management handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient
from api.orders import get_open_orders, format_orders_display, cancel_order, cancel_all_orders

logger = logging.getLogger(__name__)


async def orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display open orders for all APIs.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Fetching orders...")
    
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
    
    message = "📋 **Open Orders**\n\n"
    keyboard = []
    total_orders = 0
    
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
            
            # Get orders
            orders = await get_open_orders(client)
            await client.close()
            
            if orders is not None:
                formatted = await format_orders_display(orders)
                
                if formatted:
                    message += f"✅ **{api_name}** ({len(formatted)} order(s))\n\n"
                    
                    for order in formatted:
                        order_id = order['order_id']
                        message += f"📝 **{order['symbol']}** - {order['side']}\n"
                        message += f"├ Type: {order['order_type']}\n"
                        message += f"├ Size: {order['size']} contracts\n"
                        
                        if order['limit_price']:
                            message += f"├ Price: ${order['limit_price']}\n"
                        
                        if order['reduce_only']:
                            message += f"├ 🛡️ Reduce Only (Stop-Loss)\n"
                        
                        message += f"└ Status: {order['status']}\n\n"
                        
                        # Add cancel button
                        keyboard.append([
                            InlineKeyboardButton(
                                f"❌ Cancel Order {order['symbol']} ({order['side']})",
                                callback_data=f"order_cancel_{cred_id}_{order_id}"
                            )
                        ])
                    
                    # Add cancel all button for this API
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🗑️ Cancel All Orders ({api_name})",
                            callback_data=f"order_cancel_all_{cred_id}"
                        )
                    ])
                    
                    total_orders += len(formatted)
                else:
                    message += f"ℹ️ **{api_name}**: No open orders\n\n"
            else:
                message += f"❌ **{api_name}**: Failed to fetch orders\n\n"
        
        except Exception as e:
            logger.error(f"❌ Error fetching orders for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"
    
    if total_orders == 0:
        message += "ℹ️ No open orders across all accounts.\n"
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def order_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancel individual order.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Cancelling order...")
    
    # Parse callback data: order_cancel_{cred_id}_{order_id}
    parts = query.data.split("_")
    cred_id = parts[2]
    order_id = int(parts[3])
    
    try:
        # Get credentials
        full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
        
        if not full_cred:
            await query.edit_message_text(
                "❌ Failed to load API credentials.\n\n"
                "Use /start to return to main menu."
            )
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        
        # Cancel order
        success = await cancel_order(client, order_id)
        await client.close()
        
        if success:
            await query.edit_message_text(
                f"✅ Order {order_id} cancelled successfully.\n\n"
                f"Use /start to return to main menu."
            )
        else:
            await query.edit_message_text(
                f"❌ Failed to cancel order {order_id}.\n\n"
                f"Use /start to return to main menu."
            )
    
    except Exception as e:
        logger.error(f"❌ Error cancelling order: {e}")
        await query.edit_message_text(
            f"❌ Error cancelling order.\n\n"
            f"Use /start to return to main menu."
        )


async def order_cancel_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancel all orders for an API.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Cancelling all orders...")
    
    # Parse callback data: order_cancel_all_{cred_id}
    cred_id = query.data.replace("order_cancel_all_", "")
    
    try:
        # Get credentials
        full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
        
        if not full_cred:
            await query.edit_message_text(
                "❌ Failed to load API credentials.\n\n"
                "Use /start to return to main menu."
            )
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        
        # Cancel all orders
        cancelled_count = await cancel_all_orders(client)
        await client.close()
        
        await query.edit_message_text(
            f"✅ Cancelled {cancelled_count} order(s) successfully.\n\n"
            f"Use /start to return to main menu."
        )
    
    except Exception as e:
        logger.error(f"❌ Error cancelling all orders: {e}")
        await query.edit_message_text(
            f"❌ Error cancelling orders.\n\n"
            f"Use /start to return to main menu."
        )
      
