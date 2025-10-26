"""API credentials management handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    create_api_credential, get_api_credentials_by_user, delete_api_credential
)

logger = logging.getLogger(__name__)

# Conversation states
API_NAME, API_KEY, API_SECRET = range(3)


async def api_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display API menu with stored APIs.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Get stored APIs
    credentials = await get_api_credentials_by_user(user_id)
    
    message = "🔑 **API Credentials Management**\n\n"
    
    if credentials:
        message += f"You have {len(credentials)} API credential(s) stored:\n\n"
        for idx, cred in enumerate(credentials, 1):
            message += f"{idx}. {cred['api_name']}\n"
    else:
        message += "No API credentials stored yet.\n"
    
    message += "\nSelect an option:"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New API", callback_data="api_add")],
        [InlineKeyboardButton("🗑️ Delete API", callback_data="api_delete")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def api_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start API addition conversation."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ **Add New API Credential**\n\n"
        "Please enter a name for this API credential (e.g., 'My Delta API'):\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    
    return API_NAME


async def api_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive API name."""
    api_name = update.message.text.strip()
    
    if len(api_name) < 3:
        await update.message.reply_text("❌ API name must be at least 3 characters. Please try again:")
        return API_NAME
    
    context.user_data['api_name'] = api_name
    
    await update.message.reply_text(
        f"✅ API Name: {api_name}\n\n"
        f"Now, please enter your Delta Exchange **API Key**:\n\n"
        f"Send /cancel to abort."
    )
    
    return API_KEY


async def api_key_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive API key."""
    api_key = update.message.text.strip()
    
    if len(api_key) < 10:
        await update.message.reply_text("❌ API key seems invalid. Please try again:")
        return API_KEY
    
    context.user_data['api_key'] = api_key
    
    # Delete user's message for security
    await update.message.delete()
    
    await update.message.reply_text(
        f"✅ API Key received\n\n"
        f"Finally, please enter your Delta Exchange **API Secret**:\n\n"
        f"Send /cancel to abort."
    )
    
    return API_SECRET


async def api_secret_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive API secret and save credentials."""
    api_secret = update.message.text.strip()
    
    if len(api_secret) < 10:
        await update.message.reply_text("❌ API secret seems invalid. Please try again:")
        return API_SECRET
    
    # Delete user's message for security
    await update.message.delete()
    
    user_id = str(update.effective_user.id)
    api_name = context.user_data.get('api_name')
    api_key = context.user_data.get('api_key')
    
    try:
        # Save to database (encrypted)
        credential_id = await create_api_credential(user_id, api_name, api_key, api_secret)
        
        await update.message.reply_text(
            f"✅ **API Credential Saved Successfully!**\n\n"
            f"Name: {api_name}\n"
            f"ID: {credential_id}\n\n"
            f"Your credentials are encrypted and stored securely.\n\n"
            f"Use /start to return to main menu.",
            parse_mode="Markdown"
        )
        
        logger.info(f"✅ API credential saved for user {user_id}: {api_name}")
        
    except Exception as e:
        logger.error(f"❌ Failed to save API credential: {e}")
        await update.message.reply_text(
            f"❌ Failed to save API credential. Please try again later.\n\n"
            f"Use /start to return to main menu."
        )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END


async def api_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display APIs for deletion."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        await query.edit_message_text(
            "ℹ️ No API credentials to delete.\n\n"
            "Use /start to return to main menu."
        )
        return
    
    message = "🗑️ **Delete API Credential**\n\nSelect an API to delete:\n\n"
    
    keyboard = []
    for cred in credentials:
        cred_id = str(cred['_id'])
        api_name = cred['api_name']
        keyboard.append([InlineKeyboardButton(f"❌ {api_name}", callback_data=f"api_delete_confirm_{cred_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_api")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def api_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete API credential."""
    query = update.callback_query
    await query.answer()
    
    # Extract credential ID from callback data
    credential_id = query.data.replace("api_delete_confirm_", "")
    user_id = str(query.from_user.id)
    
    try:
        success = await delete_api_credential(credential_id, user_id)
        
        if success:
            await query.edit_message_text(
                "✅ API credential deleted successfully.\n\n"
                "Use /start to return to main menu."
            )
            logger.info(f"✅ API credential deleted: {credential_id}")
        else:
            await query.edit_message_text(
                "❌ Failed to delete API credential.\n\n"
                "Use /start to return to main menu."
            )
    
    except Exception as e:
        logger.error(f"❌ Exception deleting API credential: {e}")
        await query.edit_message_text(
            "❌ Error deleting API credential.\n\n"
            "Use /start to return to main menu."
        )


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Operation cancelled.\n\n"
        "Use /start to return to main menu."
    )
    return ConversationHandler.END
  
