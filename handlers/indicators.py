"""Indicators display handler with timeframe selection."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient
from strategy.dual_supertrend import DualSuperTrendStrategy

logger = logging.getLogger(__name__)


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
    message += "Choose a timeframe to calculate the indicator and compare with TradingView:\n"
    
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


async def indicator_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display indicator details with signal for selected timeframe.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    
    # Extract timeframe from callback data
    timeframe = query.data.replace("indicator_tf_", "")
    
    # Get indicator type from context
    indicator_type = context.user_data.get('selected_indicator', 'perusu')
    
    await query.answer(f"Calculating {indicator_type} on {timeframe}...")
    
    user_id = str(query.from_user.id)
    
    # Get first API for demo calculation
    credentials = await get_api_credentials_by_user(user_id)
    
    if not credentials:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_indicators")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
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
            
            await query.edit_message_text(
                "❌ Failed to load API credentials.",
                reply_markup=reply_markup
            )
            return
        
        # Create client
        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        
        # Calculate indicators for BTCUSD on selected timeframe
        strategy = DualSuperTrendStrategy()
        result = await strategy.calculate_indicators(client, "BTCUSD", timeframe)
        await client.close()
        
        if result:
            if indicator_type == "perusu":
                indicator_data = result['perusu']
                message = f"🟢 **Perusu Indicator (SuperTrend 20,20)**\n\n"
            else:  # sirusu
                indicator_data = result['sirusu']
                message = f"🔴 **Sirusu Indicator (SuperTrend 10,10)**\n\n"
            
            message += f"**Symbol:** BTCUSD\n"
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
            message += f"Open TradingView chart for BTCUSD on {timeframe} timeframe\n"
            message += f"Add SuperTrend indicator with same settings\n"
            message += f"└ ATR: {indicator_data['atr_length']}, Factor: {indicator_data['factor']}"
        else:
            message = f"❌ Failed to calculate {indicator_type} indicator.\n\n"
            message += "This may be due to insufficient data or API issues."
    
    except Exception as e:
        logger.error(f"❌ Error calculating indicator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        message = f"❌ Error calculating indicator:\n\n{str(e)[:200]}"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Change Timeframe", callback_data=f"indicator_select_{indicator_type}")],
        [InlineKeyboardButton("🔙 Back to Indicators", callback_data="menu_indicators")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
                                              
