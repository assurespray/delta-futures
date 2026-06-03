"""Trade Journal UI for Telegram Bot — Live + Paper Journal pages."""
import io
import csv
import asyncio
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
from database.crud import (
    get_algo_setups_by_user, get_screener_setups_by_user,
    get_archived_setups_by_user, get_archived_setup_by_id,
    get_api_credentials_by_user, delete_archived_setup_by_name
)
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

def _build_leverage_summary(trades: list) -> str:
    """Generate leverage statistics summary for paper trades."""
    from utils.market_utils import get_contract_multiplier
    valid_trades = [t for t in trades if t.get("paper_leverage") is not None]
    if valid_trades:
        leverages = [t["paper_leverage"] for t in valid_trades]
        avg_lev = sum(leverages) / len(leverages)
        min_lev = min(leverages)
        max_lev = max(leverages)
        
        notionals = []
        margins = []
        for t in valid_trades:
            asset = t.get("asset", "")
            qty = t.get("quantity", t.get("lot_size", 0))
            entry_price = t.get("entry_price", 0)
            leverage = t["paper_leverage"]
            if qty and entry_price and leverage:
                multiplier = get_contract_multiplier(asset)
                notional = float(entry_price) * float(qty) * multiplier
                notionals.append(notional)
                margins.append(notional / float(leverage))
                
        msg = (
            f"\n⚙️ **CAPITAL & LEVERAGE ANALYSIS**\n"
            f"Avg Req. Leverage: {avg_lev:.0f}x\n"
            f"Min Safest Leverage: {min_lev:.0f}x\n"
            f"Max Safest Leverage: {max_lev:.0f}x\n"
        )
        
        if notionals and margins:
            avg_notional = sum(notionals) / len(notionals)
            avg_margin = sum(margins) / len(margins)
            max_margin = max(margins)
            msg += (
                f"\n💰 **CAPITAL REQUIREMENTS (PER TRADE)**\n"
                f"Avg Position Size (1x Lev): ${avg_notional:.2f}\n"
                f"Avg Capital Required: ${avg_margin:.2f}\n"
                f"Max Capital Required: ${max_margin:.2f} (Widest SL)\n"
            )
        return msg
    return ""

# ============================================================
# LIVE JOURNAL (is_paper_trade=False) — 4-Tier Drill-Down
# Level 1: Overall → Level 2: API → Level 3: Strategy → Level 4: Asset
# ============================================================

def _format_indicator_params(indicator: str, params: dict) -> str:
    if not isinstance(params, dict):
        params = {}
        
    if indicator == 'dual_supertrend':
        return f"Dual ST (P:{params.get('perusu_atr','?')},{params.get('perusu_factor','?')} / S:{params.get('sirusu_atr','?')},{params.get('sirusu_factor','?')})"
    elif indicator == 'single_supertrend' or indicator == 'supertrend':
        return f"Single ST ({params.get('atr_length','?')}, {params.get('factor','?')})"
    elif indicator == 'range_breakout_lazybear' or indicator == 'range_breakout':
        return f"Range Breakout LB (EMA:{params.get('ema_length','?')})"
    elif indicator == 'donchian_breakout':
        return f"Donchian ({params.get('period','?')})"
    
    return indicator.replace('_', ' ').title()

def _group_strategies(strategies: list, all_setups: list) -> tuple:
    setup_map = {s.get("setup_name"): s for s in all_setups if s.get("setup_name")}
    
    groups = {}
    ungrouped = []
    
    for strat in strategies:
        setup = setup_map.get(strat)
        if not setup:
            ungrouped.append(strat)
            continue
            
        indicator = setup.get("indicator", "")
        params = setup.get("indicator_params", {})
        
        if isinstance(params, dict):
            param_key = tuple(sorted(str(v) for k, v in params.items()))
        else:
            param_key = str(params)
            
        group_key = (indicator, param_key)
        
        if group_key not in groups:
            groups[group_key] = {
                "label": _format_indicator_params(indicator, params),
                "strategies": []
            }
            
        groups[group_key]["strategies"].append(strat)
        
    final_groups = {}
    final_ungrouped = list(ungrouped)
    
    for key, data in groups.items():
        if len(data["strategies"]) == 1:
            final_ungrouped.append(data["strategies"][0])
        else:
            label = data["label"]
            counter = 2
            while label in final_groups:
                label = f"{data['label']} ({counter})"
                counter += 1
            final_groups[label] = sorted(data["strategies"])
            
    return final_groups, sorted(final_ungrouped)

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
    """Level 2: API Dashboard — stats for one API, with Active/Inactive separation."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    api_name = query.data.replace("lj_api_", "") if query.data.startswith("lj_api_") else context.user_data.get('lj_current_api', '')
    context.user_data['lj_current_api'] = api_name
    context.user_data.pop('lj_current_strategy', None)
    context.user_data.pop('lj_current_asset', None)

    current_dir = context.user_data.get('lj_direction', 'all')
    
    trades, all_strategies, algo_setups, screener_setups = await asyncio.gather(
        journal_ops.get_trades_by_asset(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir),
        journal_ops.get_traded_strategies(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id)
    )

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
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"

    # Separate active vs inactive strategies for this API
    active_live_names = set()
    for s in algo_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    for s in screener_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    
    active_strategies = [s for s in all_strategies if s in active_live_names]
    inactive_strategies = [s for s in all_strategies if s not in active_live_names]

    keyboard = [_get_dir_filter_row("lj", current_dir)]
    
    if active_strategies:
        keyboard.append([InlineKeyboardButton(f"🟢 Active Setups ({len(active_strategies)})", callback_data=f"lj_filter_active_{api_name}")])
    if inactive_strategies:
        keyboard.append([InlineKeyboardButton(f"⚪ Inactive / Archived ({len(inactive_strategies)})", callback_data=f"lj_filter_inactive_{api_name}")])

    keyboard.append([InlineKeyboardButton(f"📋 Recent Trades ({api_name})", callback_data="journal_recent_15")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Overview", callback_data="journal_dashboard")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def lj_filter_active_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show only active live strategies for the current API."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    # Extract api_name from callback data
    api_name = query.data.replace("lj_filter_active_", "")
    context.user_data['lj_current_api'] = api_name
    current_dir = context.user_data.get('lj_direction', 'all')
    
    all_strategies, algo_setups, screener_setups = await asyncio.gather(
        journal_ops.get_traded_strategies(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id)
    )
    active_live_names = set()
    for s in algo_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    for s in screener_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    
    active_strategies = [s for s in all_strategies if s in active_live_names]
    
    all_setups = algo_setups + screener_setups
    final_groups, final_ungrouped = _group_strategies(active_strategies, all_setups)
    
    if "lj_groups" not in context.user_data:
        context.user_data["lj_groups"] = {}
    
    msg = f"🟢 **Active Live Setups ({api_name})**\n"
    msg += _dir_label(current_dir) + "\n"
    
    if not active_strategies:
        msg += "No active live setups with trade history."
    else:
        msg += f"Found {len(active_strategies)} active setup(s).\nSelect one to view performance:"
    
    keyboard = [_get_dir_filter_row("lj", current_dir)]
    
    for idx, (label, strats) in enumerate(final_groups.items()):
        grp_key = f"active_{idx}"
        context.user_data["lj_groups"][grp_key] = strats
        keyboard.append([InlineKeyboardButton(f"📁 {label} ({len(strats)})", callback_data=f"lj_grp_{grp_key}")])
        
    for strat in final_ungrouped:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"lj_strat_{strat}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"lj_api_{api_name}")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def lj_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    grp_key = query.data.replace("lj_grp_", "")
    
    if "lj_groups" not in context.user_data or grp_key not in context.user_data["lj_groups"]:
        api_name = context.user_data.get('lj_current_api', '')
        await query.edit_message_text(
            "❌ Session expired. Please return to the dashboard.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data=f"lj_api_{api_name}" if api_name else "journal_dashboard")]])
        )
        return
        
    strats = context.user_data["lj_groups"][grp_key]
    current_dir = context.user_data.get('lj_direction', 'all')
    api_name = context.user_data.get('lj_current_api', '')
    
    msg = f"📁 **Strategy Group ({api_name})**\n"
    msg += _dir_label(current_dir) + "\n"
    msg += f"Select a specific setup variation:"
    
    keyboard = [_get_dir_filter_row("lj", current_dir)]
    
    for strat in strats:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"lj_strat_{strat}")])
        
    back_data = "lj_filter_inactive_" + api_name if "inactive" in grp_key else "lj_filter_active_" + api_name
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=back_data)])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def lj_filter_inactive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show only inactive/archived live strategies for the current API."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    api_name = query.data.replace("lj_filter_inactive_", "")
    context.user_data['lj_current_api'] = api_name
    current_dir = context.user_data.get('lj_direction', 'all')
    
    all_strategies, algo_setups, screener_setups, archived = await asyncio.gather(
        journal_ops.get_traded_strategies(user_id, is_paper_trade=False, api_name=api_name, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id),
        get_archived_setups_by_user(user_id, is_paper_trade=False)
    )
    active_live_names = set()
    for s in algo_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    for s in screener_setups:
        if not s.get("is_paper_trade", False):
            active_live_names.add(s.get("setup_name"))
    
    inactive_strategies = [s for s in all_strategies if s not in active_live_names]
    
    archived_by_name = {a.get("setup_name"): a for a in archived}
    
    msg = f"⚪ **Inactive / Archived Live Setups ({api_name})**\n"
    msg += _dir_label(current_dir) + "\n"
    
    if not inactive_strategies:
        msg += "No inactive live setups with trade history."
    else:
        msg += f"Found {len(inactive_strategies)} inactive setup(s).\nSelect one to view performance:"
    
    keyboard = [_get_dir_filter_row("lj", current_dir)]
    for strat in inactive_strategies:
        has_archive = "📦 " if strat in archived_by_name else "📁 "
        keyboard.append([
            InlineKeyboardButton(f"{has_archive}{strat}", callback_data=f"lj_strat_{strat}"),
            InlineKeyboardButton("🔍 Params", callback_data=f"view_arch_params_{strat}"),
            InlineKeyboardButton("🗑️", callback_data=f"wipe_strat_confirm_live_{strat}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"lj_api_{api_name}")])
    
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

    unique_assets = set(t.get("asset") for t in trades if t.get("asset"))
    assets = sorted(list(unique_assets))

    ASSETS_PER_PAGE = 14
    total_assets = len(assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = assets[start:end]

    asset_pnl = {}
    for t in trades:
        a = t.get("asset", "Unknown")
        asset_pnl[a] = asset_pnl.get(a, 0.0) + t.get("net_pnl", 0)

    keyboard = [_get_dir_filter_row("lj", current_dir)]

    row = []
    for asset in page_assets:
        pnl_icon = "🟢" if asset_pnl.get(asset, 0) >= 0 else "🔴"
        row.append(InlineKeyboardButton(f"{pnl_icon} {asset}", callback_data=f"lj_asset_{strategy}_{asset}"))
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
    """Displays the Level 1 paper journal dashboard with Active/Inactive separation."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    
    context.user_data.pop('pj_current_strategy', None)
    context.user_data.pop('pj_current_asset', None)
    
    current_dir = context.user_data.get('pj_direction', 'all')
    # Fetch all data in parallel
    trades, all_journal_strategies, algo_setups, screener_setups = await asyncio.gather(
        journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, direction=current_dir),
        journal_ops.get_traded_strategies(user_id, is_paper_trade=True, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id)
    )
    
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
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"
        msg += _build_leverage_summary(trades)

    # Separate active vs inactive strategies
    active_paper_names = set()
    for s in algo_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    for s in screener_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    
    active_strategies = [s for s in all_journal_strategies if s in active_paper_names]
    inactive_strategies = [s for s in all_journal_strategies if s not in active_paper_names]
    
    keyboard = [_get_dir_filter_row("pj", current_dir)]
    
    if active_strategies:
        keyboard.append([InlineKeyboardButton(f"🟢 Active Setups ({len(active_strategies)})", callback_data="pj_filter_active")])
    if inactive_strategies:
        keyboard.append([InlineKeyboardButton(f"⚪ Inactive / Archived ({len(inactive_strategies)})", callback_data="pj_filter_inactive")])
    
    keyboard.append([
        InlineKeyboardButton("🪙 Browse All Assets", callback_data="pj_all_assets"),
        InlineKeyboardButton("🔍 Search Asset", callback_data="pj_gsearch_start")
    ])
    
    keyboard.append([
        InlineKeyboardButton("📋 Recent 15 Trades", callback_data="pjournal_recent_15"),
        InlineKeyboardButton("📄 Export CSV", callback_data="pjournal_export_csv")
    ])
    keyboard.append([InlineKeyboardButton("🗑️ Reset Journal", callback_data="pjournal_reset_start")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def pj_filter_active_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show only active paper strategies."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    current_dir = context.user_data.get('pj_direction', 'all')
    
    all_journal_strategies, algo_setups, screener_setups = await asyncio.gather(
        journal_ops.get_traded_strategies(user_id, is_paper_trade=True, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id)
    )
    active_paper_names = set()
    for s in algo_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    for s in screener_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    
    active_strategies = [s for s in all_journal_strategies if s in active_paper_names]
    
    all_setups = algo_setups + screener_setups
    final_groups, final_ungrouped = _group_strategies(active_strategies, all_setups)
    
    if "pj_groups" not in context.user_data:
        context.user_data["pj_groups"] = {}
    
    msg = "🟢 **Active Paper Setups**\n"
    msg += _dir_label(current_dir) + "\n"
    
    if not active_strategies:
        msg += "No active paper setups with trade history."
    else:
        msg += f"Found {len(active_strategies)} active setup(s).\nSelect one to view performance:"
    
    keyboard = [_get_dir_filter_row("pj", current_dir)]
    
    for idx, (label, strats) in enumerate(final_groups.items()):
        grp_key = f"active_{idx}"
        context.user_data["pj_groups"][grp_key] = strats
        keyboard.append([InlineKeyboardButton(f"📁 {label} ({len(strats)})", callback_data=f"pj_grp_{grp_key}")])
        
    for strat in final_ungrouped:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"pj_strat_{strat}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def pj_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    grp_key = query.data.replace("pj_grp_", "")
    
    if "pj_groups" not in context.user_data or grp_key not in context.user_data["pj_groups"]:
        await query.edit_message_text(
            "❌ Session expired. Please return to the dashboard.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="paper_journal_dashboard")]])
        )
        return
        
    strats = context.user_data["pj_groups"][grp_key]
    current_dir = context.user_data.get('pj_direction', 'all')
    
    msg = f"📁 **Strategy Group**\n"
    msg += _dir_label(current_dir) + "\n"
    msg += f"Select a specific setup variation:"
    
    keyboard = [_get_dir_filter_row("pj", current_dir)]
    
    for strat in strats:
        keyboard.append([InlineKeyboardButton(f"📁 {strat}", callback_data=f"pj_strat_{strat}")])
        
    back_data = "pj_filter_inactive" if "inactive" in grp_key else "pj_filter_active"
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=back_data)])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def pj_filter_inactive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show only inactive/archived paper strategies."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    current_dir = context.user_data.get('pj_direction', 'all')
    
    all_journal_strategies, algo_setups, screener_setups, archived = await asyncio.gather(
        journal_ops.get_traded_strategies(user_id, is_paper_trade=True, direction=current_dir),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id),
        get_archived_setups_by_user(user_id, is_paper_trade=True)
    )
    active_paper_names = set()
    for s in algo_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    for s in screener_setups:
        if s.get("is_paper_trade", False):
            active_paper_names.add(s.get("setup_name"))
    
    inactive_strategies = [s for s in all_journal_strategies if s not in active_paper_names]
    
    # Also get archived setups for "View Parameters" feature
    archived_by_name = {a.get("setup_name"): a for a in archived}
    
    msg = "⚪ **Inactive / Archived Paper Setups**\n"
    msg += _dir_label(current_dir) + "\n"
    
    if not inactive_strategies:
        msg += "No inactive paper setups with trade history."
    else:
        msg += f"Found {len(inactive_strategies)} inactive setup(s).\nSelect one to view performance:"
    
    keyboard = [_get_dir_filter_row("pj", current_dir)]
    for strat in inactive_strategies:
        has_archive = "📦 " if strat in archived_by_name else "📁 "
        keyboard.append([
            InlineKeyboardButton(f"{has_archive}{strat}", callback_data=f"pj_strat_{strat}"),
            InlineKeyboardButton("🔍 Params", callback_data=f"view_arch_params_{strat}"),
            InlineKeyboardButton("🗑️", callback_data=f"wipe_strat_confirm_paper_{strat}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")])
    
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
    msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"
    msg += _build_leverage_summary(trades)
    msg += f"\nSelect an asset below:"

    unique_assets = set(t.get("asset") for t in trades if t.get("asset"))
    assets = sorted(list(unique_assets))
    
    ASSETS_PER_PAGE = 14
    total_assets = len(assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = assets[start:end]
    
    asset_pnl = {}
    for t in trades:
        a = t.get("asset", "Unknown")
        asset_pnl[a] = asset_pnl.get(a, 0.0) + t.get("net_pnl", 0)

    keyboard = [_get_dir_filter_row("pj", current_dir)]
    keyboard.append([InlineKeyboardButton("🔍 Search Asset", callback_data=f"pj_search_start_{strategy}")])
    
    row = []
    for asset in page_assets:
        pnl_icon = "🟢" if asset_pnl.get(asset, 0) >= 0 else "🔴"
        row.append(InlineKeyboardButton(f"{pnl_icon} {asset}", callback_data=f"pj_asset_{strategy}_{asset}"))
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
        msg += _build_leverage_summary(trades)

    keyboard = [
        _get_dir_filter_row("pj", current_dir),
        [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="pjournal_recent_15")],
        [InlineKeyboardButton(f"🔙 Back to {strategy}", callback_data=f"pj_strat_{strategy}")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def view_archived_params_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display the full configuration of an archived/inactive setup."""
    query = update.callback_query
    await query.answer()
    
    # callback_data = "view_arch_params_{setup_name}"
    setup_name = query.data.replace("view_arch_params_", "")
    user_id = str(query.from_user.id)
    
    # Try to find in archived_setups by name
    archived_paper, archived_live, algo_setups, screener_setups = await asyncio.gather(
        get_archived_setups_by_user(user_id, is_paper_trade=True),
        get_archived_setups_by_user(user_id, is_paper_trade=False),
        get_algo_setups_by_user(user_id),
        get_screener_setups_by_user(user_id)
    )
    archived_list = archived_paper + archived_live
    
    setup = None
    for a in archived_list:
        if a.get("setup_name") == setup_name:
            setup = a
            break
    
    # Also check active setups (user might click from active view)
    if not setup:
        for s in algo_setups + screener_setups:
            if s.get("setup_name") == setup_name:
                setup = s
                break
    
    if not setup:
        msg = f"⚠️ **Setup: {setup_name}**\n\n"
        msg += "No archived parameters found for this setup.\n"
        msg += "_This setup was deleted before the archiving system was enabled._"
    else:
        setup_type = setup.get("setup_type", "algo").upper()
        msg = f"📦 **Archived Setup Parameters**\n\n"
        msg += f"**Name:** {setup.get('setup_name', 'N/A')}\n"
        msg += f"**Type:** {setup_type}\n"
        msg += f"**Description:** {setup.get('description', 'N/A')}\n"
        msg += f"**API:** {setup.get('api_name', 'N/A')}\n"
        msg += f"**Indicator:** {setup.get('indicator', 'N/A')}\n"
        msg += f"**Direction:** {setup.get('direction', 'N/A')}\n"
        msg += f"**Timeframe:** {setup.get('timeframe', 'N/A')}\n"
        
        if setup_type == "SCREENER":
            msg += f"**Asset Selection:** {setup.get('asset_selection_type', 'N/A')}\n"
        else:
            msg += f"**Asset:** {setup.get('asset', 'N/A')}\n"
        
        msg += f"**Lot Size:** {setup.get('lot_size', 'N/A')}\n"
        msg += f"**Paper Trade:** {'Yes' if setup.get('is_paper_trade') else 'No'}\n"
        
        # Indicator parameters
        params = setup.get("indicator_params", {})
        if params:
            msg += f"\n⚙️ **Indicator Parameters:**\n"
            for k, v in params.items():
                msg += f"  {k}: {v}\n"
        
        # Preset info
        preset_name = setup.get("preset_name") or setup.get("preset_id")
        if preset_name:
            msg += f"\n**Preset:** {preset_name}\n"
        
        # Archive date
        archived_at = setup.get("archived_at")
        if archived_at:
            from datetime import timedelta
            if hasattr(archived_at, 'strftime'):
                ist = archived_at + timedelta(hours=5, minutes=30)
                msg += f"\n_Archived: {ist.strftime('%Y-%m-%d %H:%M IST')}_"
    
    # Back button - go to the strategy's journal page
    keyboard = [
        [InlineKeyboardButton(f"📁 View Trades ({setup_name})", callback_data=f"pj_strat_{setup_name}")],
        [InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")]
    ]
    
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def wipe_strategy_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation before permanently wiping a strategy."""
    query = update.callback_query
    await query.answer()
    
    # callback_data = "wipe_strat_confirm_{paper|live}_{strategy_name}"
    data = query.data.replace("wipe_strat_confirm_", "")
    if data.startswith("paper_"):
        mode = "paper"
        strategy_name = data[6:]
    elif data.startswith("live_"):
        mode = "live"
        strategy_name = data[5:]
    else:
        return
    
    msg = (
        f"⚠️ **DELETE STRATEGY PERMANENTLY**\n\n"
        f"**Strategy:** {strategy_name}\n"
        f"**Type:** {'Paper' if mode == 'paper' else 'Live'}\n\n"
        f"This will permanently delete:\n"
        f"  - All trade history for this strategy\n"
        f"  - Archived setup parameters\n"
        f"  - P&L records from your journal\n\n"
        f"**This action CANNOT be undone.**"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Yes, Delete Everything", callback_data=f"wipe_strat_exec_{mode}_{strategy_name}")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="pj_filter_inactive" if mode == "paper" else "journal_dashboard")]
    ]
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def wipe_strategy_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanently wipe a strategy: delete all journal trades + archived params."""
    query = update.callback_query
    await query.answer("Deleting...")
    user_id = str(query.from_user.id)
    
    data = query.data.replace("wipe_strat_exec_", "")
    if data.startswith("paper_"):
        is_paper = True
        strategy_name = data[6:]
    elif data.startswith("live_"):
        is_paper = False
        strategy_name = data[5:]
    else:
        return
    
    # Delete journal trades and archived setup in parallel
    deleted_count, _ = await asyncio.gather(
        journal_ops.wipe_strategy(user_id, strategy_name, is_paper_trade=is_paper),
        delete_archived_setup_by_name(user_id, strategy_name)
    )
    
    msg = (
        f"✅ **Strategy Wiped Successfully**\n\n"
        f"**Strategy:** {strategy_name}\n"
        f"**Trades Deleted:** {deleted_count}\n"
        f"**Archived Params:** Removed\n\n"
        f"This strategy has been permanently erased."
    )
    
    back_data = "pj_filter_inactive" if is_paper else "journal_dashboard"
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=back_data)]]
    
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
        msg += _build_leverage_summary(trades)

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
        leverage = t.get('paper_leverage', 'N/A')
        
        msg += f"{emoji} **{t_asset}** ({direction}) | ${pnl:.2f}\n"
        msg += f"   Setup: {setup_name}\n"
        msg += f"   Entry: ${t.get('entry_price', 0):.4f} ({entry_time}) | Exit: ${t.get('exit_price', 0):.4f} ({exit_time})\n"
        msg += f"   Fees: ${t.get('total_fees', 0):.2f} | Reason: {t.get('exit_reason', 'unknown')}\n"
        msg += f"   Req. Leverage: {leverage}x\n\n"
        
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
# GLOBAL ASSET FILTER — Browse & Search across ALL strategies
# ============================================================

async def pj_all_assets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Browse all traded assets across every strategy (paginated grid with stats)."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    # Parse page from callback_data: pj_all_assets or pj_all_assets:p2
    page = 0
    if ":p" in query.data:
        try:
            page = int(query.data.rsplit(":p", 1)[1])
        except:
            page = 0

    context.user_data.pop('pj_current_strategy', None)
    context.user_data.pop('pj_current_asset', None)

    current_dir = context.user_data.get('pj_direction', 'all')

    # Fetch all trades and all unique assets in parallel
    trades, assets = await asyncio.gather(
        journal_ops.get_trades_by_asset(user_id, is_paper_trade=True, direction=current_dir),
        journal_ops.get_traded_assets(user_id, is_paper_trade=True, direction=current_dir)
    )

    if not assets:
        keyboard = [
            _get_dir_filter_row("pj", current_dir),
            [InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")]
        ]
        await query.edit_message_text(
            "🪙 **Browse All Assets**\n" + _dir_label(current_dir) + "\nNo traded assets found.",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return

    # Build per-asset stats from trades
    asset_stats = {}
    for t in trades:
        a = t.get("asset", "Unknown")
        if a not in asset_stats:
            asset_stats[a] = {"count": 0, "net_pnl": 0.0}
        asset_stats[a]["count"] += 1
        asset_stats[a]["net_pnl"] += t.get("net_pnl", 0)

    # Sort assets by trade count descending
    sorted_assets = sorted(assets, key=lambda a: asset_stats.get(a, {}).get("count", 0), reverse=True)

    total_trades = len(trades)
    total_pnl = sum(t.get("net_pnl", 0) for t in trades)

    ASSETS_PER_PAGE = 14
    total_assets = len(sorted_assets)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_assets = sorted_assets[start:end]

    msg = "🪙 **Browse All Assets**\n"
    msg += _dir_label(current_dir)
    msg += f"📊 {total_assets} assets | {total_trades} trades | Net P&L: ${total_pnl:.2f}\n"
    if total_pages > 1:
        msg += f"Page {page + 1}/{total_pages}\n"
    msg += "\nSelect an asset to view details:"

    keyboard = [_get_dir_filter_row("pj", current_dir)]

    row = []
    for asset in page_assets:
        stats = asset_stats.get(asset, {"count": 0, "net_pnl": 0.0})
        pnl_icon = "🟢" if stats["net_pnl"] >= 0 else "🔴"
        label = f"{pnl_icon} {asset} ({stats['count']})"
        row.append(InlineKeyboardButton(label, callback_data=f"pj_global_asset_{asset}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Pagination
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pj_all_assets:p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"pj_all_assets:p{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="paper_journal_dashboard")])

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def pj_global_asset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cross-strategy asset detail: stats for one asset across ALL strategies."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    asset = query.data.replace("pj_global_asset_", "")
    current_dir = context.user_data.get('pj_direction', 'all')

    context.user_data.pop('pj_current_strategy', None)
    context.user_data['pj_current_asset'] = asset

    trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, direction=current_dir)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
    fees = sum(t.get("total_fees", 0) for t in trades)
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)

    msg = f"🪙 **Asset:** {asset}\n"
    msg += _dir_label(current_dir)

    if total_trades == 0:
        msg += "No trades found."
    else:
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"
        msg += _build_leverage_summary(trades)

        # Per-strategy breakdown
        strat_stats = {}
        for t in trades:
            s = t.get("strategy_name", "Unknown")
            if s not in strat_stats:
                strat_stats[s] = {"count": 0, "net_pnl": 0.0}
            strat_stats[s]["count"] += 1
            strat_stats[s]["net_pnl"] += t.get("net_pnl", 0)

        if len(strat_stats) > 1:
            msg += f"\n📁 **Per Strategy:**\n"
            for s, data in sorted(strat_stats.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
                p = data["net_pnl"]
                msg += f"  {s}: {data['count']} trades | ${p:+.2f}\n"

    keyboard = [
        _get_dir_filter_row("pj", current_dir),
        [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="pjournal_recent_15")],
        [InlineKeyboardButton("🔙 Back to Assets", callback_data="pj_all_assets")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def pj_gsearch_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start global asset search (across all strategies)."""
    query = update.callback_query
    await query.answer()

    # Clear strategy scope so this is a global search
    context.user_data.pop('pj_current_strategy', None)
    context.user_data.pop('pj_current_asset', None)

    keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="paper_journal_dashboard")]]
    await query.edit_message_text(
        "🔍 **Search Asset (All Strategies)**\n\n"
        "Type the asset name or partial match (e.g. BTC, ethusd, SOL):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return 1


async def pj_gsearch_receive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for global asset search."""
    from telegram.ext import ConversationHandler
    search_term = update.message.text.strip().upper()
    user_id = str(update.effective_user.id)
    current_dir = context.user_data.get('pj_direction', 'all')

    assets = await journal_ops.get_traded_assets(user_id, is_paper_trade=True, direction=current_dir)
    matches = [a for a in assets if search_term in a.upper()]

    if not matches:
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="paper_journal_dashboard")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        await update.message.reply_text(
            f"❌ No assets matching '{search_term}' found.\n"
            "Try again or go back.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return 1

    if len(matches) == 1:
        asset = matches[0]
        context.user_data['pj_current_asset'] = asset
        context.user_data.pop('pj_current_strategy', None)

        trades = await journal_ops.get_trades_by_asset(user_id, asset=asset, is_paper_trade=True, direction=current_dir)
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        gross_pnl = sum(t.get("gross_pnl", 0) for t in trades)
        fees = sum(t.get("total_fees", 0) for t in trades)
        net_pnl = sum(t.get("net_pnl", 0) for t in trades)

        msg = f"✅ Match found: **{asset}**\n\n"
        msg += f"🪙 **Asset:** {asset}\n"
        msg += _dir_label(current_dir)
        msg += f"📈 Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
        msg += f"💵 Gross P&L: ${gross_pnl:.2f}\n"
        msg += f"🏦 Simulated Fees: ${fees:.2f}\n"
        msg += f"🔥 **Net P&L: ${net_pnl:.2f}**\n"
        msg += _build_leverage_summary(trades)

        # Per-strategy breakdown
        strat_stats = {}
        for t in trades:
            s = t.get("strategy_name", "Unknown")
            if s not in strat_stats:
                strat_stats[s] = {"count": 0, "net_pnl": 0.0}
            strat_stats[s]["count"] += 1
            strat_stats[s]["net_pnl"] += t.get("net_pnl", 0)

        if len(strat_stats) > 1:
            msg += f"\n📁 **Per Strategy:**\n"
            for s, data in sorted(strat_stats.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
                p = data["net_pnl"]
                msg += f"  {s}: {data['count']} trades | ${p:+.2f}\n"

        keyboard = [
            [InlineKeyboardButton(f"📋 Recent Trades ({asset})", callback_data="pjournal_recent_15")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="paper_journal_dashboard")]
        ]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END

    # Multiple matches — show button grid
    context.user_data.pop('pj_current_asset', None)

    msg = f"🔍 Multiple assets match '{search_term}'. Select one:\n"
    keyboard = []
    row = []
    for asset in matches:
        row.append(InlineKeyboardButton(f"🪙 {asset}", callback_data=f"pj_global_asset_{asset}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="paper_journal_dashboard")])

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ConversationHandler.END


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
    elif asset:
        # Global asset view (no strategy scope)
        await pj_global_asset_callback(update, context)
    else:
        await paper_journal_dashboard_callback(update, context)
