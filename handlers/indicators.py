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
        [InlineKeyboardButton("🟢🔴 Both (20,20 & 10,10)", callback_data="indicator_select_both")],
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
    elif indicator_type == "sirusu":
        message = "🔴 **Sirusu Indicator (SuperTrend 10,10)**\n\n"
    else:
        message = "🟢🔴 **Both Indicators (20,20 & 10,10)**\n\n"
    
    message += "**Select Timeframe:**\n\n"
    message += "Choose a timeframe to calculate the indicator:\n"
    
    keyboard = [
        [
            InlineKeyboardButton("1m", callback_data="indicator_tf_1m"),
            InlineKeyboardButton("5m", callback_data="indicator_tf_5m"),
            InlineKeyboardButton("15m", callback_data="indicator_tf_15m")
        ],
        [
            InlineKeyboardButton("30m", callback_data="indicator_tf_30m"),
            InlineKeyboardButton("1h", callback_data="indicator_tf_1h"),
            InlineKeyboardButton("4h", callback_data="indicator_tf_4h")
        ],
        [
            InlineKeyboardButton("1d", callback_data="indicator_tf_1d")
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
    
    await query.answer(f"Selected {timeframe} timeframe")
    
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
    """Calculate and display indicator after receiving asset symbol."""
    asset = update.message.text.strip().upper()
    
    # Get stored context
    indicator_type = context.user_data.get('selected_indicator', 'perusu')
    timeframe = context.user_data.get('selected_timeframe', '15m')
    
    # Store asset for refresh
    context.user_data['selected_asset'] = asset
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"⏳ Calculating {indicator_type} for {asset} on {timeframe}...\n\n"
        "This may take a few seconds."
    )
    
    # Calculate and display
    await _calculate_and_display_indicator(
        processing_msg, context, asset, indicator_type, timeframe
    )
    
    return ConversationHandler.END


async def indicator_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh indicator calculation."""
    query = update.callback_query
    await query.answer("Refreshing indicator...")
    
    # Get stored context
    indicator_type = context.user_data.get('selected_indicator', 'perusu')
    timeframe = context.user_data.get('selected_timeframe', '15m')
    asset = context.user_data.get('selected_asset', 'BTCUSD')
    
    # Update message to show loading
    await query.edit_message_text(
        f"⏳ Refreshing {indicator_type} for {asset} on {timeframe}...\n\n"
        "This may take a few seconds.",
        parse_mode="Markdown"
    )
    
    # Calculate and display
    await _calculate_and_display_indicator(
        query.message, context, asset, indicator_type, timeframe, is_refresh=True
    )


async def _calculate_and_display_indicator(message, context, asset, indicator_type, timeframe, is_refresh=False):
    """
    Helper function to calculate and display indicator.
    
    Args:
        message: Telegram message object to edit
        context: Callback context
        asset: Trading symbol
        indicator_type: 'perusu' or 'sirusu'
        timeframe: Timeframe string
        is_refresh: Whether this is a refresh operation
    """
    user_id = str(message.chat.id) if hasattr(message, 'chat') else str(context._user_id)
    
    # Get first API for calculation
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_indicators")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(
            "ℹ️ No API credentials stored.\n\n"
            "Please add API credentials first to view indicator signals.",
            reply_markup=reply_markup
        )
        return
    
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
            
            await message.edit_text(
                "❌ Failed to load API credentials.",
                reply_markup=reply_markup
            )
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        
        # Calculate indicators for selected asset and timeframe
        strategy = DualSuperTrendStrategy()
        result = await strategy.calculate_indicators(client, asset, timeframe)
        await client.close()

        indicator_type = context.user_data.get('selected_indicator', 'perusu')
        
        if result:
            if indicator_type == "perusu":
                indicator_data = result['perusu']
                msg = f"🟢 **Perusu Indicator (SuperTrend 20,20)**\n\n"
            elif indicator_type == "sirusu":
                indicator_data = result['sirusu']
                msg = f"🔴 **Sirusu Indicator (SuperTrend 10,10)**\n\n"
            elif indicator_type == "both":
                perusu = result['perusu']
                sirusu = result['sirusu']
                msg = (
                    f"🟢 **Perusu Indicator (SuperTrend 20,20)**\n"
                    f"├ ATR Length: {perusu['atr_length']}\n"
                    f"├ Factor: {perusu['factor']}\n"
                    f"├ ATR Value: {perusu['atr']}\n"
                    f"├ Current Price: ${perusu['latest_close']}\n"
                    f"├ Signal: {'📈' if perusu['signal'] == 1 else '📉'} {perusu['signal_text']}\n"
                    f"└ SuperTrend Value: ${perusu['supertrend_value']}\n\n"
                    f"🔴 **Sirusu Indicator (SuperTrend 10,10)**\n"
                    f"├ ATR Length: {sirusu['atr_length']}\n"
                    f"├ Factor: {sirusu['factor']}\n"
                    f"├ ATR Value: {sirusu['atr']}\n"
                    f"├ Current Price: ${sirusu['latest_close']}\n"
                    f"├ Signal: {'📈' if sirusu['signal'] == 1 else '📉'} {sirusu['signal_text']}\n"
                    f"└ SuperTrend Value: ${sirusu['supertrend_value']}\n\n"
                )
        else:
            msg = f"❌ Failed to calculate indicator(s) for {asset}.\n\n" # (as before)
            # Get precision for formatting
            precision = indicator_data.get('precision', 2)
            
            msg += f"**Symbol:** {asset}\n"
            msg += f"**Timeframe:** {timeframe}\n"
            msg += f"**API Account:** {api_name}\n"
            msg += f"**Candles Used:** {result.get('candles_used', 100)}\n\n"
            msg += f"**Details:**\n"
            msg += f"├ ATR Length: {indicator_data['atr_length']}\n"
            msg += f"├ Factor: {indicator_data['factor']}\n"
            msg += f"├ ATR Value: {indicator_data['atr']}\n"
            msg += f"└ Current Price: ${indicator_data['latest_close']}\n\n"
            
            signal_emoji = "📈" if indicator_data['signal'] == 1 else "📉"
            msg += f"**Signal:** {signal_emoji} **{indicator_data['signal_text']}**\n"
            msg += f"**SuperTrend Value:** ${indicator_data['supertrend_value']}\n\n"
            
            if indicator_data['signal'] == 1:
                msg += f"💡 Price is above SuperTrend line (Uptrend)\n"
            else:
                msg += f"💡 Price is below SuperTrend line (Downtrend)\n"
            
            msg += f"\n📋 **Compare with TradingView:**\n"
            msg += f"1. Open {asset} chart on {timeframe} timeframe\n"
            msg += f"2. Add SuperTrend indicator\n"
            msg += f"3. Set ATR: {indicator_data['atr_length']}, Factor: {indicator_data['factor']}\n"
            msg += f"4. Compare values!"
        else:
            msg = f"❌ Failed to calculate {indicator_type} indicator for {asset}.\n\n"
            msg += "**Possible reasons:**\n"
            msg += "• Invalid symbol (check Delta Exchange product list)\n"
            msg += "• Insufficient market data\n"
            msg += "• API connection issues\n\n"
            msg += "💡 Try a different symbol or timeframe"
    
    except Exception as e:
        logger.error(f"❌ Error calculating indicator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        msg = f"❌ Error calculating indicator for {asset}:\n\n{str(e)[:200]}\n\n"
        msg += "Please check if the symbol is valid on Delta Exchange."
    
    # Add refresh button
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="indicator_refresh")],
        [InlineKeyboardButton("🔄 Try Another Asset", callback_data=f"indicator_select_{indicator_type}")],
        [InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown")


async def cancel_indicator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel indicator calculation."""
    keyboard = [[InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❌ Indicator calculation cancelled.",
        reply_markup=reply_markup
    )
    
    return ConversationHandler.END
