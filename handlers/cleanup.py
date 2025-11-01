"""Cleanup handlers for orphaned orders."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from api.delta_client import DeltaExchangeClient
from api.orders import cancel_all_orphaned_stop_orders, get_open_orders
from database.crud import get_api_credentials_by_user, get_api_credential_by_id

logger = logging.getLogger(__name__)


async def cleanup_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display cleanup menu."""
    query = update.callback_query
    await query.answer()
    
    message = (
        "🧹 **Cleanup Options**\n\n"
        "Manage orphaned orders and clean up your account.\n\n"
        "Select an option:"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Cancel Orphaned Stop Orders", callback_data="cleanup_select_api")],
        [InlineKeyboardButton("📋 View Open Orders", callback_data="cleanup_view_orders")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def cleanup_select_api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select API account for cleanup."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Get user's APIs
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        await query.edit_message_text(
            "❌ No API credentials found.\n\n"
            "Please add API credentials first from the API Menu."
        )
        return
    
    message = "🔍 **Select API Account**\n\nChoose which account to cleanup:\n"
    
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([
            InlineKeyboardButton(
                f"🔑 {api_name}",
                callback_data=f"cleanup_start_{cred_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="cleanup_menu_callback")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def cleanup_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start cleanup process."""
    query = update.callback_query
    await query.answer()
    
    # Extract API ID
    api_id = query.data.replace("cleanup_start_", "")
    
    try:
        # Get API credentials
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        
        if not cred:
            await query.edit_message_text("❌ API credentials not found.")
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=cred['api_key'],
            api_secret=cred['api_secret']
        )
        
        api_name = cred['api_name']
        
        # Get all products to iterate through
        logger.info(f"🔍 Starting cleanup for {api_name}...")
        
        # Send status message
        status_msg = await query.edit_message_text(
            f"🔍 **Cleanup in Progress**\n\n"
            f"API: {api_name}\n"
            f"Status: Scanning for orphaned stop orders...\n\n"
            f"Please wait..."
        )
        
        # Get all untriggered stop orders across all products
        params = {"state": "untriggered"}
        response = await client.get("/v2/orders", params)
        
        untriggered_orders = []
        if response and response.get("success"):
            untriggered_orders = response.get("result", [])
        
        if not untriggered_orders:
            await query.edit_message_text(
                f"✅ **Cleanup Complete**\n\n"
                f"API: {api_name}\n"
                f"Status: No orphaned stop orders found.\n\n"
                f"Your account is clean! ✨"
            )
            await client.close()
            return
        
        # Show orders to confirm deletion
        message = (
            f"⚠️ **Found {len(untriggered_orders)} Untriggered Stop Orders**\n\n"
            f"The following orders will be cancelled:\n\n"
        )
        
        for i, order in enumerate(untriggered_orders[:5], 1):  # Show first 5
            symbol = order.get("product", {}).get("symbol", "Unknown")
            stop_price = order.get("stop_price", 0)
            message += f"{i}. {symbol} - Stop @ ${stop_price}\n"
        
        if len(untriggered_orders) > 5:
            message += f"\n... and {len(untriggered_orders) - 5} more\n"
        
        message += f"\nTotal: {len(untriggered_orders)} orders\n\n"
        message += "⚠️ **This action cannot be undone!**"
        
        keyboard = [
            [InlineKeyboardButton(
                f"✅ Cancel All {len(untriggered_orders)} Orders",
                callback_data=f"cleanup_confirm_{api_id}"
            )],
            [InlineKeyboardButton("❌ Cancel", callback_data="cleanup_menu_callback")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Store the count for the next step
        context.user_data['cleanup_count'] = len(untriggered_orders)
        
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ Cleanup error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:100]}")


async def cleanup_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and execute cleanup."""
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("cleanup_confirm_", "")
    
    try:
        # Get API credentials
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        
        if not cred:
            await query.edit_message_text("❌ API credentials not found.")
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=cred['api_key'],
            api_secret=cred['api_secret']
        )
        
        api_name = cred['api_name']
        
        # Send processing message
        await query.edit_message_text(
            f"🔄 **Processing Cleanup**\n\n"
            f"API: {api_name}\n"
            f"Status: Cancelling orphaned stop orders...\n\n"
            f"This may take a moment..."
        )
        
        # Get all untriggered orders
        params = {"state": "untriggered"}
        response = await client.get("/v2/orders", params)
        
        untriggered_orders = response.get("result", []) if response and response.get("success") else []
        
        cancelled_count = 0
        failed_count = 0
        
        # Cancel each order
        for order in untriggered_orders:
            order_id = order.get("id")
            symbol = order.get("product", {}).get("symbol", "Unknown")
            
            try:
                cancel_response = await client.delete(f"/v2/orders/{order_id}")
                
                if cancel_response and cancel_response.get("success"):
                    cancelled_count += 1
                    logger.info(f"✅ Cancelled order {order_id} ({symbol})")
                else:
                    failed_count += 1
                    logger.warning(f"⚠️ Failed to cancel {order_id}")
                    
            except Exception as e:
                if "404" in str(e):
                    cancelled_count += 1  # 404 means already gone
                else:
                    failed_count += 1
                    logger.error(f"❌ Error cancelling {order_id}: {e}")
        
        await client.close()
        
        # Show results
        message = f"✅ **Cleanup Complete!**\n\n"
        message += f"API: {api_name}\n\n"
        message += f"**Results:**\n"
        message += f"✅ Cancelled: {cancelled_count}\n"
        message += f"❌ Failed: {failed_count}\n"
        message += f"📊 Total: {cancelled_count + failed_count}\n\n"
        
        if failed_count == 0:
            message += "Your account is now clean! ✨"
        else:
            message += f"⚠️ {failed_count} orders could not be cancelled."
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Cleanup Menu", callback_data="cleanup_menu_callback")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        
        logger.info(f"✅ Cleanup complete: {cancelled_count} cancelled, {failed_count} failed")
        
    except Exception as e:
        logger.error(f"❌ Cleanup confirmation error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:100]}")


async def cleanup_view_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all open orders."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        await query.edit_message_text("❌ No API credentials found.")
        return
    
    message = "📋 **Select API Account**\n\nChoose which account to view:\n"
    
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([
            InlineKeyboardButton(
                f"📊 {api_name}",
                callback_data=f"cleanup_view_start_{cred_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="cleanup_menu_callback")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def cleanup_view_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View open orders for selected API."""
    query = update.callback_query
    await query.answer()
    
    api_id = query.data.replace("cleanup_view_start_", "")
    
    try:
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        
        if not cred:
            await query.edit_message_text("❌ API credentials not found.")
            return
        
        client = DeltaExchangeClient(
            api_key=cred['api_key'],
            api_secret=cred['api_secret']
        )
        
        api_name = cred['api_name']
        
        # Get all open orders
        orders = await get_open_orders(client)
        
        await client.close()
        
        if not orders:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="cleanup_view_orders_callback")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📋 **Open Orders - {api_name}**\n\n"
                f"ℹ️ No open orders found.\n\n"
                f"Your account is clean!",
                reply_markup=reply_markup
            )
            return
        
        # Count order types
        open_orders = [o for o in orders if o.get("state") == "open"]
        untriggered_orders = [o for o in orders if o.get("state") == "untriggered"]
        
        message = f"📋 **Open Orders - {api_name}**\n\n"
        message += f"**Summary:**\n"
        message += f"📊 Total: {len(orders)}\n"
        message += f"⏳ Open: {len(open_orders)}\n"
        message += f"🎯 Untriggered: {len(untriggered_orders)}\n\n"
        
        if untriggered_orders:
            message += f"**Untriggered Stop Orders:**\n"
            for order in untriggered_orders[:10]:
                symbol = order.get("product", {}).get("symbol", "Unknown")
                stop_price = order.get("stop_price", 0)
                order_type = order.get("order_type", "unknown")
                message += f"• {symbol} - {order_type} @ ${stop_price}\n"
            
            if len(untriggered_orders) > 10:
                message += f"... and {len(untriggered_orders) - 10} more\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="cleanup_view_orders_callback")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ View orders error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:100]}")
      
