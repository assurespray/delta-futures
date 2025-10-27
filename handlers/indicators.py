"""Indicators display handler with timeframe and asset selection."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient
from strategy.dual_supertrend import DualSuperTrendStrategy

logger = logging.getLogger(__name__)

# Conversation states
INDICATOR_ASSET = 0


async def indicators_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display indicator selection menu.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer()
    
    message = "📊 **Indicators**\n\n"
    message += "Select an indicator to view current signal:\n\n"
    message += "• **Perusu** (SuperTrend 20,20) - Entry indicator\n"
    message += "• **Sirusu** (SuperTrend 10,10) - Exit indicator\n"
    
    keyboard = [
        [InlineKeyboardButton("🟢 Perusu (20,20)", callback_data="indicator_select_perusu")],
        [InlineKeyboardButton("🔴 Sirusu (10,10)", callback_data="indicator_select_sirusu")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def indicator_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display timeframe selection after indicator selection.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    
    # Extract indicator type from callback data
    indicator_type = query.data.replace("indicator_select_", "")
    await query.answer(f"Select timeframe for {indicator_type}")
    
    # Store indicator type in user context
    context.user_data['selected_indicator'] = indicator_type
    
    if indicator_type == "perusu":
        message = "🟢 **Perusu Indicator (SuperTrend 20,20)**\n\n"
    else:
        message = "🔴 **Sirusu Indicator (SuperTrend 10,10)**\n\n"
    
    message += "**Select Timeframe:**\n\n"
    message += "Choose a timeframe to calculate the indicator:\n"
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data=f"indicator_tf_1m"),
            InlineKeyboardButton("5m", callback_data=f"indicator_tf_5m"),
            InlineKeyboardButton("15m", callback_data=f"indicator_tf_15m")
        ],
        [
            InlineKeyboardButton("30m", callback_data=f"indicator_tf_30m"),
            InlineKeyboardButton("1h", callback_data=f"indicator_tf_1h"),
            InlineKeyboardButton("4h", callback_data=f"indicator_tf_4h")
        ],
        [
            InlineKeyboardButton("1d", callback_data=f"indicator_tf_1d")
        ],
        [InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def indicator_timeframe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ask for asset symbol after timeframe selection.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    
    # Extract timeframe from callback data
    timeframe = query.data.replace("indicator_tf_", "")
    
    # Store timeframe in context
    context.user_data['selected_timeframe'] = timeframe
    
    # Get indicator type
    indicator_type = context.user_data.get('selected_indicator', 'perusu')
    
    await query.answer(f"Enter asset symbol")
    
    message = f"**{'🟢 Perusu' if indicator_type == 'perusu' else '🔴 Sirusu'} Indicator**\n\n"
    message += f"**Timeframe:** {timeframe}\n\n"
    message += "**Enter Trading Symbol:**\n\n"
    message += "Type the asset symbol you want to analyze.\n\n"
    message += "**Examples:**\n"
    message += "• BTCUSD - Bitcoin futures\n"
    message += "• ETHUSD - Ethereum futures\n"
    message += "• SOLUSD - Solana futures\n"
    message += "• XRPUSD - Ripple futures\n\n"
    message += "💡 **Tip:** Symbol must match exactly as on Delta Exchange\n\n"
    message += "Send /cancel to abort"
    
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="menu_indicators")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
    return INDICATOR_ASSET


async def indicator_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Calculate and display indicator after receiving asset symbol.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    asset = update.message.text.strip().upper()
    
    # Get stored context
    indicator_type = context.user_data.get('selected_indicator', 'perusu')
    timeframe = context.user_data.get('selected_timeframe', '15m')
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"⏳ Calculating {indicator_type} for {asset} on {timeframe}...\n\n"
        "This may take a few seconds."
    )
    
    user_id = str(update.message.from_user.id)
    
    # Get first API for calculation
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_indicators")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await processing_msg.edit_text(
            "ℹ️ No API credentials stored.\n\n"
            "Please add API credentials first to view indicator signals.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    # Use first API
    cred = credentials[0]
    api_name = cred['api_name']
    cred_id = str(cred['_id'])
    
    try:
        # Get credentials
        full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
        
        if not full_cred:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_indicators")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await processing_msg.edit_text(
                "❌ Failed to load API credentials.",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
        
        # Create client
        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        
        # Calculate indicators for selected asset and timeframe
        strategy = DualSuperTrendStrategy()
        result = await strategy.calculate_indicators(client, asset, timeframe)
        await client.close()
        
        if result:
            if indicator_type == "perusu":
                indicator_data = result['perusu']
                message = f"🟢 **Perusu Indicator (SuperTrend 20,20)**\n\n"
            else:  # sirusu
                indicator_data = result['sirusu']
                message = f"🔴 **Sirusu Indicator (SuperTrend 10,10)**\n\n"
            
            message += f"**Symbol:** {asset}\n"
            message += f"**Timeframe:** {timeframe}\n"
            message += f"**API Account:** {api_name}\n"
            message += f"**Candles Used:** {result.get('candles_used', 100)}\n\n"
            message += f"**Details:**\n"
            message += f"├ ATR Length: {indicator_data['atr_length']}\n"
            message += f"├ Factor: {indicator_data['factor']}\n"
            message += f"├ ATR Value: {indicator_data['atr']}\n"
            message += f"└ Current Price: ${indicator_data['latest_close']:,.2f}\n\n"
            
            signal_emoji = "📈" if indicator_data['signal'] == 1 else "📉"
            message += f"**Signal:** {signal_emoji} **{indicator_data['signal_text']}**\n"
            message += f"**SuperTrend Value:** ${indicator_data['supertrend_value']:,.2f}\n\n"
            
            if indicator_data['signal'] == 1:
                message += f"💡 Price is above SuperTrend line (Uptrend)\n"
            else:
                message += f"💡 Price is below SuperTrend line (Downtrend)\n"
            
            message += f"\n📋 **Compare with TradingView:**\n"
            message += f"1. Open {asset} chart on {timeframe} timeframe\n"
            message += f"2. Add SuperTrend indicator\n"
            message += f"3. Set ATR: {indicator_data['atr_length']}, Factor: {indicator_data['factor']}\n"
            message += f"4. Compare values!"
        else:
            message = f"❌ Failed to calculate {indicator_type} indicator for {asset}.\n\n"
            message += "**Possible reasons:**\n"
            message += "• Invalid symbol (check Delta Exchange product list)\n"
            message += "• Insufficient market data\n"
            message += "• API connection issues\n\n"
            message += "💡 Try a different symbol or timeframe"
    
    except Exception as e:
        logger.error(f"❌ Error calculating indicator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        message = f"❌ Error calculating indicator for {asset}:\n\n{str(e)[:200]}\n\n"
        message += "Please check if the symbol is valid on Delta Exchange."
    
    keyboard = [
        [InlineKeyboardButton("🔄 Try Another Asset", callback_data=f"indicator_select_{indicator_type}")],
        [InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await processing_msg.edit_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
    return ConversationHandler.END


async def cancel_indicator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel indicator calculation."""
    keyboard = [[InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❌ Indicator calculation cancelled.",
        reply_markup=reply_markup
    )
    
    return ConversationHandler.END
    
