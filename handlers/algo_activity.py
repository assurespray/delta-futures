"""Algo activity (trade history) handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_algo_activity_by_user
from datetime import datetime

logger = logging.getLogger(__name__)


async def algo_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display last 3 days of algo trading activity.
    
    Args:
        update: Telegram update
        context: Callback context
    """
    query = update.callback_query
    await query.answer("Fetching activity...")
    
    user_id = str(query.from_user.id)
    
    # Get last 3 days of activity
    activities = await get_algo_activity_by_user(user_id, days=3)
    
    if not activities:
        keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ No trading activity in the last 3 days.",
            reply_markup=reply_markup
        )
        return
    
    message = "📜 **Algo Trading Activity (Last 3 Days)**\n\n"
    
    total_pnl_usd = 0.0
    total_pnl_inr = 0.0
    winning_trades = 0
    losing_trades = 0
    
    for activity in activities:
        setup_name = activity['algo_setup_name']
        asset = activity['asset']
        direction = activity['direction'].upper()
        lot_size = activity['lot_size']
        entry_price = activity['entry_price']
        entry_time = activity['entry_time']
        
        is_closed = activity.get('is_closed', False)
        
        if is_closed:
            exit_price = activity.get('exit_price', 0)
            exit_time = activity.get('exit_time')
            pnl = activity.get('pnl', 0)
            pnl_inr = activity.get('pnl_inr', 0)
            
            if pnl >= 0:
                pnl_emoji = "🟢"
                winning_trades += 1
            else:
                pnl_emoji = "🔴"
                losing_trades += 1
            
            total_pnl_usd += pnl
            total_pnl_inr += pnl_inr
            
            message += f"{'─' * 30}\n"
            message += f"📊 **{setup_name}** - {asset}\n"
            message += f"Direction: {direction} | Size: {lot_size}\n\n"
            message += f"🔵 Entry: ${entry_price} | {entry_time.strftime('%m/%d %H:%M')}\n"
            message += f"🔴 Exit: ${exit_price} | {exit_time.strftime('%m/%d %H:%M')}\n"
            message += f"{pnl_emoji} PnL: ${pnl:.2f} (₹{pnl_inr:.2f})\n\n"
        else:
            # Open position
            message += f"{'─' * 30}\n"
            message += f"📊 **{setup_name}** - {asset}\n"
            message += f"Direction: {direction} | Size: {lot_size}\n\n"
            message += f"🔵 Entry: ${entry_price} | {entry_time.strftime('%m/%d %H:%M')}\n"
            message += f"⏳ Position Still Open\n\n"
    
    message += f"{'═' * 30}\n"
    message += f"**Summary:**\n"
    message += f"Total Trades: {winning_trades + losing_trades}\n"
    message += f"Winning: {winning_trades} | Losing: {losing_trades}\n"
    
    if winning_trades + losing_trades > 0:
        win_rate = (winning_trades / (winning_trades + losing_trades)) * 100
        message += f"Win Rate: {win_rate:.1f}%\n"
    
    total_pnl_emoji = "🟢" if total_pnl_usd >= 0 else "🔴"
    message += f"\n{total_pnl_emoji} **Total PnL: ${total_pnl_usd:.2f} (₹{total_pnl_inr:.2f})**"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
  
