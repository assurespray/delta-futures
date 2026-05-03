"""Trade Journal UI for Telegram Bot."""
import io
import csv
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.journal import journal_ops
from config.settings import settings

logger = logging.getLogger(__name__)

async def journal_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main journal dashboard with optional asset filtering."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    selected_asset = None
    if "journal_filter_" in query.data:
        selected_asset = query.data.split("journal_filter_")[-1]
    
    trades = await journal_ops.get_trades_by_asset(user_id, selected_asset)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    header = f"📊 **Trade Journal** ({selected_asset if selected_asset else 'All Assets'})\n\n"
    
    if total_trades == 0:
        msg = header + "No recorded trades found."
    else:
        msg = header
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Exchange Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"

    # Build Asset Filter Keyboard
    assets = await journal_ops.get_traded_assets(user_id)
    keyboard = []
    
    if selected_asset:
        keyboard.append([InlineKeyboardButton("🔙 View All Assets", callback_data="journal_dashboard")])
        
    row = []
    for asset in assets:
        if asset != selected_asset:
            row.append(InlineKeyboardButton(f"🪙 {asset}", callback_data=f"journal_filter_{asset}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
    if row: keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("📋 Recent 15 Trades", callback_data="journal_recent_15"),
        InlineKeyboardButton("📄 Export CSV", callback_data="journal_export_csv")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_recent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the 15 most recent trades."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_recent_trades(user_id, limit=15)
    if not trades:
        await query.edit_message_text("No recent trades found.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="journal_dashboard")]]))
        return
        
    msg = "📋 **Last 15 Journal Entries**\n\n"
    for t in trades:
        asset = t.get('asset', '?')
        direction = t.get('direction', '?').upper()
        pnl = t.get('net_pnl', 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        
        msg += f"{emoji} **{asset}** ({direction}) | ${pnl:.2f}\n"
        msg += f"   Entry: ${t.get('entry_price', 0):.4f} | Exit: ${t.get('exit_price', 0):.4f}\n"
        msg += f"   Fees: ${t.get('total_fees', 0):.2f} | Reason: {t.get('exit_reason', 'unknown')}\n\n"
        
    keyboard = [[InlineKeyboardButton("🔙 Dashboard", callback_data="journal_dashboard")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends a CSV of all trades."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_trades_by_asset(user_id)
    if not trades:
        await context.bot.send_message(chat_id=query.message.chat_id, text="No trades to export.")
        return
        
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Trade ID", "Setup", "Asset", "Direction", "Quantity", 
        "Entry Time", "Entry Price", "Exit Time", "Exit Price", 
        "Exit Reason", "Gross PnL", "Total Fees", "Net PnL"
    ])
    
    for t in trades:
        writer.writerow([
            t.get('trade_id', ''),
            t.get('strategy_name', ''),
            t.get('asset', ''),
            t.get('direction', ''),
            t.get('quantity', ''),
            t.get('entry_time', ''),
            t.get('entry_price', ''),
            t.get('exit_time', ''),
            t.get('exit_price', ''),
            t.get('exit_reason', ''),
            round(t.get('gross_pnl', 0), 4),
            round(t.get('total_fees', 0), 4),
            round(t.get('net_pnl', 0), 4)
        ])
        
    buf = io.BytesIO()
    buf.write(output.getvalue().encode('utf-8'))
    buf.seek(0)
    
    filename = f"JournalExport_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=buf,
        filename=filename,
        caption="📄 Here is your complete Trade Journal export."
    )
