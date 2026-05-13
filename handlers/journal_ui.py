"""Trade Journal UI for Telegram Bot — Live + Paper Journal pages."""
import io
import csv
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.journal import journal_ops
from config.settings import settings

logger = logging.getLogger(__name__)


# ============================================================
# LIVE JOURNAL (is_paper_trade=False)
# ============================================================

async def journal_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the live journal dashboard with optional asset filtering."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    selected_asset = None
    if "journal_filter_" in query.data:
        selected_asset = query.data.split("journal_filter_")[-1]
    
    trades = await journal_ops.get_trades_by_asset(user_id, selected_asset, is_paper_trade=False)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    header = f"📊 **Live Trade Journal** ({selected_asset if selected_asset else 'All Assets'})\n\n"
    
    if total_trades == 0:
        msg = header + "No recorded live trades found."
    else:
        msg = header
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Exchange Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"

    # Build Asset Filter Keyboard
    assets = await journal_ops.get_traded_assets(user_id, is_paper_trade=False)
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
    """Displays the 15 most recent live trades."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_recent_trades(user_id, limit=15, is_paper_trade=False)
    if not trades:
        await query.edit_message_text("No recent live trades found.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="journal_dashboard")]]))
        return
        
    msg = "📋 **Last 15 Live Journal Entries**\n\n"
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
    """Generates and sends a CSV of all live trades."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=False)
    if not trades:
        await context.bot.send_message(chat_id=query.message.chat_id, text="No live trades to export.")
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
    
    filename = f"LiveJournal_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=buf,
        filename=filename,
        caption="📄 Here is your complete Live Trade Journal export."
    )


# ============================================================
# PAPER JOURNAL (is_paper_trade=True)
# ============================================================

async def paper_journal_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the Level 1 paper journal dashboard (Overall Stats & Strategy List)."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    header = f"📄 **Paper Trade Journal (Overall)**\n\n"
    
    if total_trades == 0:
        msg = header + "No recorded paper trades found."
    else:
        msg = header
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
        msg += "Select a Strategy below to view its specific performance:"

    strategies = await journal_ops.get_traded_strategies(user_id, is_paper_trade=True)
    keyboard = []
    
    for strat in strategies:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"pj_strat_{strat}")])
    
    keyboard.append([
        InlineKeyboardButton("📋 Recent 15 Trades", callback_data="pjournal_recent_15"),
        InlineKeyboardButton("📄 Export CSV", callback_data="pjournal_export_csv")
    ])
    keyboard.append([InlineKeyboardButton("🗑️ Reset Journal", callback_data="pjournal_reset_start")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def pjournal_strategy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays Level 2: Strategy Stats & Paginated Asset List."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    data = query.data.replace("pj_strat_", "")
    page = 0
    if "_p" in data:
        parts = data.rsplit("_p", 1)
        strategy = parts[0]
        try:
            page = int(parts[1])
        except:
            page = 0
    else:
        strategy = data

    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, strategy=strategy)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    msg = f"📁 **Strategy:** {strategy}\n\n"
    msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
    msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
    msg += "Select an asset below:"

    assets = await journal_ops.get_traded_assets_by_strategy(user_id, strategy, is_paper_trade=True)
    
    ASSETS_PER_PAGE = 14
    total_assets = len(assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = assets[start:end]
    
    keyboard = []
    keyboard.append([InlineKeyboardButton("🔍 Search Asset", callback_data=f"pj_search_start_{strategy}")])
    
    row = []
    for asset in page_assets:
        row.append(InlineKeyboardButton(f"🪙 {asset}", callback_data=f"pj_asset_{strategy}_{asset}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pj_strat_{strategy}_p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"pj_strat_{strategy}_p{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Strategies", callback_data="paper_journal_dashboard")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def pjournal_asset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays Level 3: Asset Details for a specific strategy."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    data = query.data.replace("pj_asset_", "")
    parts = data.rsplit("_", 1)
    if len(parts) != 2:
        await query.edit_message_text("Error parsing asset.")
        return
    strategy, asset = parts[0], parts[1]
    
    trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, strategy=strategy)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    msg = f"🪙 **Asset:** {asset}\n"
    msg += f"📁 **Strategy:** {strategy}\n\n"
    
    if total_trades == 0:
        msg += "No trades found."
    else:
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

    keyboard = [[InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def pjournal_search_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start conversation to search for an asset in a strategy."""
    query = update.callback_query
    await query.answer()
    
    strategy = query.data.replace("pj_search_start_", "")
    context.user_data['pj_search_strategy'] = strategy
    
    keyboard = [[InlineKeyboardButton("🔙 Cancel Search", callback_data=f"pj_strat_{strategy}")]]
    await query.edit_message_text(
        f"🔍 **Search Asset**\n\n"
        f"Please type the name of the asset you want to look up (e.g., BTC, ethusd) for the strategy `{strategy}`:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return 1

async def pjournal_search_receive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the text input for asset search."""
    search_term = update.message.text.strip().upper()
    strategy = context.user_data.get('pj_search_strategy')
    user_id = str(update.effective_user.id)
    
    from telegram.ext import ConversationHandler
    if not strategy:
        await update.message.reply_text("❌ Session expired. Use /start to return to menu.")
        return ConversationHandler.END
        
    assets = await journal_ops.get_traded_assets_by_strategy(user_id, strategy, is_paper_trade=True)
    
    matches = [a for a in assets if search_term in a.upper()]
    
    if not matches:
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Strategy", callback_data=f"pj_strat_{strategy}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        await update.message.reply_text(
            f"❌ No assets matching '{search_term}' found in `{strategy}`.\n"
            "Try again or go back.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return 1
        
    if len(matches) == 1:
        asset = matches[0]
        trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, strategy=strategy)
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
        fees = sum(t.get("total_fees", 0) for t in trades)
        net_pnl = sum(t.get("net_pnl", 0) for t in trades)
        
        msg = f"✅ Match found: **{asset}**\n\n"
        msg += f"🪙 **Asset:** {asset}\n"
        msg += f"📁 **Strategy:** {strategy}\n\n"
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

        keyboard = [[InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")]]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END
        
    msg = f"🔍 Multiple assets match '{search_term}'. Select one:\n"
    keyboard = []
    row = []
    for asset in matches:
        row.append(InlineKeyboardButton(f"🪙 {asset}", callback_data=f"pj_asset_{strategy}_{asset}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    keyboard.append([InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")])
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ConversationHandler.END


async def pjournal_reset_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for confirmation before resetting the paper journal."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("⚠️ YES, DELETE EVERYTHING", callback_data="pjournal_reset_execute")],
        [InlineKeyboardButton("NO, CANCEL", callback_data="paper_journal_dashboard")]
    ]
    await query.edit_message_text(
        "⚠️ **WARNING: Reset Paper Journal**\n\n"
        "This will permanently delete all your **closed and cancelled** paper trades.\n"
        "Your active open positions and pending orders will remain intact, but your historical Win Rate, P&L, and trade list will be completely wiped to start fresh.\n\n"
        "Are you absolutely sure you want to proceed?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def pjournal_reset_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the paper journal reset."""
    query = update.callback_query
    await query.answer("Wiping journal...")
    user_id = str(query.from_user.id)
    
    from database.crud import delete_closed_paper_trades
    
    success_j = await journal_ops.clear_paper_journal(user_id)
    success_c = await delete_closed_paper_trades(user_id)
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="paper_journal_dashboard")]]
    if success_j and success_c:
        msg = "✅ **Paper Journal Reset Successful**\n\nAll historical paper trades have been wiped. Your paper P&L is now $0.00."
    else:
        msg = "❌ **Error during reset.**\n\nSome records may not have been fully deleted."
        
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def paper_journal_recent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the 15 most recent paper trades."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_recent_trades(user_id, limit=15, is_paper_trade=True)
    if not trades:
        await query.edit_message_text("No recent paper trades found.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")]]))
        return
        
    msg = "📋 **Last 15 Paper Journal Entries**\n\n"
    for t in trades:
        asset = t.get('asset', '?')
        direction = t.get('direction', '?').upper()
        pnl = t.get('net_pnl', 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        
        msg += f"{emoji} **{asset}** ({direction}) | ${pnl:.2f}\n"
        msg += f"   Entry: ${t.get('entry_price', 0):.4f} | Exit: ${t.get('exit_price', 0):.4f}\n"
        msg += f"   Fees: ${t.get('total_fees', 0):.2f} | Reason: {t.get('exit_reason', 'unknown')}\n\n"
        
    keyboard = [[InlineKeyboardButton("🔙 Dashboard", callback_data="paper_journal_dashboard")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def paper_journal_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends a CSV of all paper trades."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    user_id = str(query.from_user.id)
    
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True)
    if not trades:
        await context.bot.send_message(chat_id=query.message.chat_id, text="No paper trades to export.")
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
    
    filename = f"PaperJournal_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=buf,
        filename=filename,
        caption="📄 Here is your complete Paper Trade Journal export."
    )
