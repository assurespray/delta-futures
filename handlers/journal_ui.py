"""Trade Journal UI for Telegram Bot — Live + Paper Journal pages."""
import io
import csv
import logging

def to_ist_str(dt) -> str:
    if not dt: return "N/A"
    if isinstance(dt, str):
        from datetime import datetime
        try: dt = datetime.fromisoformat(dt)
        except: return dt
    from datetime import timedelta
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime('%m/%d %H:%M')
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.journal import journal_ops
from config.settings import settings

logger = logging.getLogger(__name__)


def _get_dir_filter_row(prefix: str, current_dir: str) -> list:
    """Generate direction filter button row. prefix='lj' or 'pj'."""
    options = [("all", "Both"), ("long", "Long"), ("short", "Short")]
    row = []
    for val, label in options:
        check = "✅ " if current_dir == val else ""
        row.append(InlineKeyboardButton(f"{check}{label}", callback_data=f"{prefix}_set_dir_{val}"))
    return row


def _dir_label(current_dir: str) -> str:
    """Return a header line indicating active direction filter."""
    if current_dir == "long":
        return "📗 Showing: Long trades only\n"
    elif current_dir == "short":
        return "📕 Showing: Short trades only\n"
    return ""


# ============================================================
# LIVE JOURNAL (is_paper_trade=False) — 4-Tier Drill-Down
# Level 1: Overall → Level 2: API → Level 3: Strategy → Level 4: Asset
# ============================================================

async def journal_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Level 1: Overall Dashboard — stats across all APIs, lists API buttons."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    # Clear live journal navigation context
    context.user_data.pop('lj_current_api', None)
    context.user_data.pop('lj_current_strategy', None)
    context.user_data.pop('lj_current_asset', None)

    current_dir = context.user_data.get('lj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=False, direction=current_dir)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)

    header = "📊 **Live Trade Journal (Overall)**\n"
    header += _dir_label(current_dir) + "\n"

    if total_trades == 0:
        msg = header + "No recorded live trades found."
    else:
        msg = header
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Exchange Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
        msg += "Select an API below to view its performance:"

    # Build API list keyboard
    api_names = await journal_ops.get_traded_api_names(user_id, is_paper_trade=False, direction=current_dir)
    keyboard = [_get_dir_filter_row("lj", current_dir)]

    for api in api_names:
        keyboard.append([InlineKeyboardButton(f"🔑 {api}", callback_data=f"lj_api_{api}")])

    keyboard.append([
        InlineKeyboardButton("📋 Recent 15 Trades", callback_data="journal_recent_15"),
        InlineKeyboardButton("📄 Export CSV", callback_data="journal_export_csv")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Level 2: API Dashboard — stats for one API, lists strategy buttons."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    api_name = query.data.replace("lj_api_", "") if query.data.startswith("lj_api_") else context.user_data.get('lj_current_api', '')
    context.user_data['lj_current_api'] = api_name
    context.user_data.pop('lj_current_strategy', None)
    context.user_data.pop('lj_current_asset', None)

    current_dir = context.user_data.get('lj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)

    msg = f"🔑 **API:** {api_name}\n"
    msg += _dir_label(current_dir) + "\n"

    if total_trades == 0:
        msg += "No trades found for this API."
    else:
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Exchange Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
        msg += "Select a Strategy below:"

    strategies = await journal_ops.get_traded_strategies(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir)
    keyboard = [_get_dir_filter_row("lj", current_dir)]

    for strat in strategies:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"lj_strat_{strat}")])

    keyboard.append([InlineKeyboardButton(f"📋 Recent Trades ({api_name})", callback_data="journal_recent_15")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Overview", callback_data="journal_dashboard")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_strategy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Level 3: Strategy Dashboard — stats for one strategy in one API, paginated asset list."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    api_name = context.user_data.get('lj_current_api')
    if not api_name:
        await query.edit_message_text("❌ Session expired. Please start from the Journal Dashboard.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="journal_dashboard")]]))
        return

    if query.data.startswith("lj_strat_"):
        data = query.data.replace("lj_strat_", "")
        page = 0
        if ":p" in data:
            parts = data.rsplit(":p", 1)
            strategy = parts[0]
            try:
                page = int(parts[1])
            except:
                page = 0
        else:
            strategy = data
    else:
        # Called from set_dir redirect — use stored context
        strategy = context.user_data.get('lj_current_strategy', '')
        page = 0

    context.user_data['lj_current_strategy'] = strategy
    context.user_data.pop('lj_current_asset', None)

    current_dir = context.user_data.get('lj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=False, strategy=strategy, api_name=api_name, direction=current_dir)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    net_pnl = sum(t.get("net_pnl", 0) for t in trades)

    msg = f"🔑 **API:** {api_name}\n"
    msg += f"📁 **Strategy:** {strategy}\n"
    msg += _dir_label(current_dir) + "\n"
    msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
    msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
    msg += "Select an asset below:"

    assets = await journal_ops.get_traded_assets_by_strategy(user_id, strategy, is_paper_trade=False, api_name=api_name, direction=current_dir)

    ASSETS_PER_PAGE = 14
    total_assets = len(assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = assets[start:end]

    keyboard = [_get_dir_filter_row("lj", current_dir)]

    row = []
    for asset in page_assets:
        row.append(InlineKeyboardButton(f"🪙 {asset}", callback_data=f"lj_asset_{strategy}_{asset}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"lj_strat_{strategy}:p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"lj_strat_{strategy}:p{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(f"📋 Recent Trades ({strategy})", callback_data="journal_recent_15")])
    keyboard.append([InlineKeyboardButton(f"🔙 Back to {api_name}", callback_data=f"lj_api_{api_name}")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_asset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Level 4: Asset Details for a specific strategy and API."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    api_name = context.user_data.get('lj_current_api')
    if not api_name:
        await query.edit_message_text("❌ Session expired. Please start from the Journal Dashboard.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="journal_dashboard")]]))
        return

    if query.data.startswith("lj_asset_"):
        data = query.data.replace("lj_asset_", "")
        parts = data.rsplit("_", 1)
        if len(parts) != 2:
            await query.edit_message_text("Error parsing asset.")
            return
        strategy, asset = parts[0], parts[1]
    else:
        # Called from set_dir redirect — use stored context
        strategy = context.user_data.get('lj_current_strategy', '')
        asset = context.user_data.get('lj_current_asset', '')

    context.user_data['lj_current_strategy'] = strategy
    context.user_data['lj_current_asset'] = asset

    current_dir = context.user_data.get('lj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=False, strategy=strategy, api_name=api_name, direction=current_dir)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)

    msg = f"🔑 **API:** {api_name}\n"
    msg += f"📁 **Strategy:** {strategy}\n"
    msg += f"🪙 **Asset:** {asset}\n"
    msg += _dir_label(current_dir) + "\n"

    if total_trades == 0:
        msg += "No trades found."
    else:
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Exchange Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

    keyboard = [
        _get_dir_filter_row("lj", current_dir),
        [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="journal_recent_15")],
        [InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"lj_strat_{strategy}")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_recent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Context-aware: Displays the 15 most recent live trades, filtered by current drill-down level."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    api_name = context.user_data.get('lj_current_api')
    strategy = context.user_data.get('lj_current_strategy')
    asset = context.user_data.get('lj_current_asset')
    current_dir = context.user_data.get('lj_direction', 'all')

    trades = await journal_ops.get_recent_trades(
        user_id, limit=15, is_paper_trade=False,
        strategy=strategy, asset=asset, api_name=api_name, direction=current_dir
    )

    # Determine back button based on drill-down level
    if strategy and asset:
        back_btn = f"lj_asset_{strategy}_{asset}"
    elif strategy:
        back_btn = f"lj_strat_{strategy}"
    elif api_name:
        back_btn = f"lj_api_{api_name}"
    else:
        back_btn = "journal_dashboard"

    if not trades:
        await query.edit_message_text("No recent live trades found.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=back_btn)]]))
        return

    # Build header based on context
    if strategy and asset:
        msg = f"📋 **Last 15 Live Trades ({asset})**\n\n"
    elif strategy:
        msg = f"📋 **Last 15 Live Trades ({strategy})**\n\n"
    elif api_name:
        msg = f"📋 **Last 15 Live Trades ({api_name})**\n\n"
    else:
        msg = "📋 **Last 15 Live Journal Entries**\n\n"

    for t in trades:
        t_asset = t.get('asset', '?')
        direction = t.get('direction', '?').upper()
        pnl = t.get('net_pnl', 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        setup_name = t.get('strategy_name') or t.get('setup_name') or '?'

        entry_time = to_ist_str(t.get('entry_time'))
        exit_time = to_ist_str(t.get('exit_time'))

        msg += f"{emoji} **{t_asset}** ({direction}) | ${pnl:.2f}\n"
        msg += f"   Setup: {setup_name}\n"
        msg += f"   Entry: ${t.get('entry_price', 0):.4f} ({entry_time}) | Exit: ${t.get('exit_price', 0):.4f} ({exit_time})\n"
        msg += f"   Fees: ${t.get('total_fees', 0):.2f} | Reason: {t.get('exit_reason', 'unknown')}\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=back_btn)]]

    if len(msg) > 4000:
        msg = msg[:3997] + "..."

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def journal_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends a CSV of all live trades (respects direction filter)."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    user_id = str(query.from_user.id)

    current_dir = context.user_data.get('lj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=False, direction=current_dir)
    if not trades:
        await context.bot.send_message(chat_id=query.message.chat_id, text="No live trades to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Trade ID", "API", "Setup", "Asset", "Direction", "Quantity",
        "Entry Time", "Entry Price", "Exit Time", "Exit Price",
        "Exit Reason", "Gross PnL", "Total Fees", "Net PnL"
    ])

    for t in trades:
        writer.writerow([
            t.get('trade_id', ''),
            t.get('api_name', 'DeltaExchange'),
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
    
    context.user_data.pop('pj_current_strategy', None)
    context.user_data.pop('pj_current_asset', None)
    
    current_dir = context.user_data.get('pj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, direction=current_dir)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    header = f"📄 **Paper Trade Journal (Overall)**\n"
    header += _dir_label(current_dir) + "\n"
    
    if total_trades == 0:
        msg = header + "No recorded paper trades found."
    else:
        msg = header
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
        msg += "Select a Strategy below to view its specific performance:"

    strategies = await journal_ops.get_traded_strategies(user_id, is_paper_trade=True, direction=current_dir)
    keyboard = [_get_dir_filter_row("pj", current_dir)]
    
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
    
    if query.data.startswith("pj_strat_"):
        data = query.data.replace("pj_strat_", "")
        page = 0
        if ":p" in data:
            parts = data.rsplit(":p", 1)
            strategy = parts[0]
            try:
                page = int(parts[1])
            except:
                page = 0
        else:
            strategy = data
    else:
        # Called from set_dir redirect — use stored context
        strategy = context.user_data.get('pj_current_strategy', '')
        page = 0

    context.user_data['pj_current_strategy'] = strategy
    context.user_data.pop('pj_current_asset', None)

    current_dir = context.user_data.get('pj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, strategy=strategy, direction=current_dir)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    msg = f"📁 **Strategy:** {strategy}\n"
    msg += _dir_label(current_dir) + "\n"
    msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
    msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n\n"
    msg += "Select an asset below:"

    assets = await journal_ops.get_traded_assets_by_strategy(user_id, strategy, is_paper_trade=True, direction=current_dir)
    
    ASSETS_PER_PAGE = 14
    total_assets = len(assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = assets[start:end]
    
    keyboard = [_get_dir_filter_row("pj", current_dir)]
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
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pj_strat_{strategy}:p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"pj_strat_{strategy}:p{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton(f"📋 Recent Trades ({strategy})", callback_data="pjournal_recent_15")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Strategies", callback_data="paper_journal_dashboard")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def pjournal_asset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays Level 3: Asset Details for a specific strategy."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    if query.data.startswith("pj_asset_"):
        data = query.data.replace("pj_asset_", "")
        parts = data.rsplit("_", 1)
        if len(parts) != 2:
            await query.edit_message_text("Error parsing asset.")
            return
        strategy, asset = parts[0], parts[1]
    else:
        # Called from set_dir redirect — use stored context
        strategy = context.user_data.get('pj_current_strategy', '')
        asset = context.user_data.get('pj_current_asset', '')

    context.user_data['pj_current_strategy'] = strategy
    context.user_data['pj_current_asset'] = asset
    
    current_dir = context.user_data.get('pj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, strategy=strategy, direction=current_dir)
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    msg = f"🪙 **Asset:** {asset}\n"
    msg += f"📁 **Strategy:** {strategy}\n"
    msg += _dir_label(current_dir) + "\n"
    
    if total_trades == 0:
        msg += "No trades found."
    else:
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

    keyboard = [
        _get_dir_filter_row("pj", current_dir),
        [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="pjournal_recent_15")],
        [InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")]
    ]
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
        
    assets = await journal_ops.get_traded_assets_by_strategy(user_id, strategy, is_paper_trade=True, direction=context.user_data.get('pj_direction', 'all'))
    
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
        context.user_data['pj_current_strategy'] = strategy
        context.user_data['pj_current_asset'] = asset
        
        trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, strategy=strategy, direction=context.user_data.get('pj_direction', 'all'))
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
        fees = sum(t.get("total_fees", 0) for t in trades)
        net_pnl = sum(t.get("net_pnl", 0) for t in trades)
        
        msg = f"✅ Match found: **{asset}**\n\n"
        msg += f"🪙 **Asset:** {asset}\n"
        msg += f"📁 **Strategy:** {strategy}\n"
        msg += _dir_label(context.user_data.get('pj_direction', 'all')) + "\n"
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

        keyboard = [
        [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="pjournal_recent_15")],
        [InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")]
    ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END
        
    context.user_data['pj_current_strategy'] = strategy
    context.user_data.pop('pj_current_asset', None)
    
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
    
    strategy = context.user_data.get('pj_current_strategy')
    asset = context.user_data.get('pj_current_asset')
    current_dir = context.user_data.get('pj_direction', 'all')
    
    trades = await journal_ops.get_recent_trades(user_id, limit=15, is_paper_trade=True, strategy=strategy, asset=asset, direction=current_dir)
    
    if strategy and asset:
        back_btn = f"pj_asset_{strategy}_{asset}"
    elif strategy:
        back_btn = f"pj_strat_{strategy}"
    else:
        back_btn = "paper_journal_dashboard"
        
    if not trades:
        await query.edit_message_text("No recent paper trades found.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=back_btn)]]))
        return
        
    if strategy and asset:
        msg = f"📋 **Last 15 Paper Trades ({asset})**\n\n"
    elif strategy:
        msg = f"📋 **Last 15 Paper Trades ({strategy})**\n\n"
    else:
        msg = "📋 **Last 15 Paper Journal Entries**\n\n"
        
    for t in trades:
        t_asset = t.get('asset', '?')
        direction = t.get('direction', '?').upper()
        pnl = t.get('net_pnl', 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        setup_name = t.get('strategy_name') or t.get('setup_name') or '?'
        
        entry_time = to_ist_str(t.get('entry_time'))
        exit_time = to_ist_str(t.get('exit_time'))
        
        msg += f"{emoji} **{t_asset}** ({direction}) | ${pnl:.2f}\n"
        msg += f"   Setup: {setup_name}\n"
        msg += f"   Entry: ${t.get('entry_price', 0):.4f} ({entry_time}) | Exit: ${t.get('exit_price', 0):.4f} ({exit_time})\n"
        msg += f"   Fees: ${t.get('total_fees', 0):.2f} | Reason: {t.get('exit_reason', 'unknown')}\n\n"
        
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=back_btn)]]
    
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
        
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def paper_journal_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends a CSV of all paper trades (respects direction filter)."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    user_id = str(query.from_user.id)
    
    context.user_data.pop('pj_current_strategy', None)
    context.user_data.pop('pj_current_asset', None)
    
    current_dir = context.user_data.get('pj_direction', 'all')
    trades = await journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, direction=current_dir)
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


# ============================================================
# DIRECTION FILTER TOGGLE CALLBACKS
# ============================================================

async def journal_set_dir_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle direction filter for Live Journal and re-render current view."""
    query = update.callback_query
    await query.answer()

    new_dir = query.data.replace("lj_set_dir_", "")
    context.user_data['lj_direction'] = new_dir

    # Re-render the current view by determining drill-down level
    strategy = context.user_data.get('lj_current_strategy')
    asset = context.user_data.get('lj_current_asset')
    api_name = context.user_data.get('lj_current_api')

    if strategy and asset:
        await journal_asset_callback(update, context)
    elif strategy:
        await journal_strategy_callback(update, context)
    elif api_name:
        await journal_api_callback(update, context)
    else:
        await journal_dashboard_callback(update, context)


async def pjournal_set_dir_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle direction filter for Paper Journal and re-render current view."""
    query = update.callback_query
    await query.answer()

    new_dir = query.data.replace("pj_set_dir_", "")
    context.user_data['pj_direction'] = new_dir

    # Re-render the current view by determining drill-down level
    strategy = context.user_data.get('pj_current_strategy')
    asset = context.user_data.get('pj_current_asset')

    if strategy and asset:
        await pjournal_asset_callback(update, context)
    elif strategy:
        await pjournal_strategy_callback(update, context)
    else:
        await paper_journal_dashboard_callback(update, context)
