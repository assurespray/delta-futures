"""Indicators display handler with timeframe and asset selection."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

# Conversation states
INDICATOR_ASSET = 0


from database.crud import get_strategy_presets_by_user, ensure_default_presets

async def indicators_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    message = "📊 **Indicators**\n\nSelect an indicator preset to view current signals:\n\n"
    
    keyboard = []
    for preset in presets:
        pid = str(preset['_id'])
        name = preset.get('preset_name', 'Unnamed')
        keyboard.append([InlineKeyboardButton(f"🟢 {name}", callback_data=f"indicator_select_{pid}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    return ConversationHandler.END


from database.crud import get_strategy_preset_by_id

async def indicator_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    preset_id = query.data.replace("indicator_select_", "")
    preset = await get_strategy_preset_by_id(preset_id)
    await query.answer(f"Select timeframe")
    
    if preset:
        context.user_data['selected_preset_id'] = preset_id
        context.user_data['selected_indicator'] = preset['strategy_type']
        name = preset.get('preset_name', 'Strategy')
    else:
        name = "Unknown Strategy"
        
    message = f"🟢 **{name}**\n\n**Select Timeframe:**\n\nChoose a timeframe:\n"
    
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
    
    preset_id = context.user_data.get('selected_preset_id')
    from database.crud import get_strategy_preset_by_id
    preset = await get_strategy_preset_by_id(preset_id)
    name = preset.get('preset_name', 'Strategy') if preset else 'Strategy'
    
    await query.answer(f"Selected {timeframe} timeframe")
    
    message = f"**🟢 {name}**\n\n"
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
    user_id = str(message.chat.id) if hasattr(message, 'chat') else str(context._user_id)
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

    cred = credentials[0]
    api_name = cred['api_name']
    cred_id = str(cred['_id'])

    try:
        full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
        if not full_cred:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_indicators")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.edit_text(
                "❌ Failed to load API credentials.",
                reply_markup=reply_markup
            )
            return

        client = DeltaExchangeClient(
            api_key=full_cred['api_key'],
            api_secret=full_cred['api_secret']
        )
        preset_id = context.user_data.get('selected_preset_id')
        preset = await get_strategy_preset_by_id(preset_id)
        from strategy.factory import StrategyFactory
        strategy = StrategyFactory.get_strategy(preset['strategy_type'], preset['parameters'])
        result = await strategy.calculate_indicators(
            client, asset, timeframe,
            skip_boundary_check=True,
            force_recalc=True
        )
        await client.close()

        indicator_type = context.user_data.get('selected_indicator', 'perusu')
        msg = ""

        logger.info(f"Returned indicator result for both: {result}")

        # NEW: handle candle-not-ready diagnostic
        if result and isinstance(result, dict) and result.get("error") == "candle_not_ready":
            wait = int(result.get("wait_time", 0))
            minutes = max(1, wait // 60)
            msg = (
                f"⚠️ Current {timeframe} candle for {asset} is still forming.\n\n"
                f"Approximate time until next closed candle: **{minutes} minute(s)**.\n\n"
                "Please try again closer to the next candle close."
            )
        else:
            if result:
                # Info block
                info = (
                    f"🔹 **Symbol:** {asset}\n"
                    f"🔹 **Timeframe:** {timeframe}\n"
                    f"🔹 **API Account:** {api_name}\n"
                    f"🔹 **Candles Used:** {result.get('candles_used', 100)}\n\n"
                )

                ptype = preset['strategy_type']
                name = preset.get('preset_name', 'Indicator')
                if ptype == "dual_supertrend":
                    p, s = result['perusu'], result['sirusu']
                    msg = (
                        info +
                        f"🟢 **Dual SuperTrend**\n"
                        f"├ P Signal: {'📈' if p['signal']==1 else '📉'} {p['signal_text']} (${p['supertrend_value']:.4f})\n"
                        f"└ S Signal: {'📈' if s['signal']==1 else '📉'} {s['signal_text']} (${s['supertrend_value']:.4f})\n"
                    )
                elif ptype == "single_supertrend":
                    d = result['single_st']
                    msg = (
                        info +
                        f"🟢 **Single SuperTrend**\n"
                        f"├ Signal: {'📈' if d['signal']==1 else '📉'} {d['signal_text']}\n"
                        f"└ ST Value: ${d['supertrend_value']:.4f}\n"
                    )
                elif ptype == "range_breakout_lazybear":
                    d = result['range_data']
                    msg = (
                        info +
                        f"🏔️ **LazyBear Range Breakout**\n"
                        f"├ Trend Phase: {'📈 LONG' if d['trend_phase']==1 else '📉 SHORT'}\n"
                        f"├ Range Up: ${d['up']:.4f}\n"
                        f"├ Range Down: ${d['down']:.4f}\n"
                        f"├ Range Mid: ${d['mid']:.4f}\n"
                        f"├ EMA (34): ${d['ema']:.4f}\n"
                        f"└ Breakout Signal: {d['signal_text']}\n"
                    )
            else:
                msg = f"❌ Failed to calculate indicator(s) for {asset}.\n\n"
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

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="indicator_refresh")],
        [InlineKeyboardButton("🔄 Try Another Asset", callback_data=f"indicator_select_{preset_id}")],
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
