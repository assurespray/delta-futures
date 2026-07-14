"""
Paper Trading Telegram Menu Handler.

Fully modular: delete this file and remove references in bot.py
and start.py to completely remove paper trading UI.

Features:
- Create/View/Delete paper trading setups (Individual + Screener)
- View open paper positions
- Set virtual balance to any amount
- Toggle paper mode on existing setups
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config.constants import ASSET_TYPE_TEXT, ASSET_TYPE_TEXT_SHORT
from database.crud import (
    get_api_credentials_by_user, create_algo_setup,
    get_algo_setups_by_paper_mode, get_algo_setup_by_id,
    delete_algo_setup, update_algo_setup,
    get_paper_balance, reset_paper_balance, get_open_trade_by_setup,
    get_open_paper_positions,
    create_screener_setup, get_screener_setups_by_paper_mode,
    get_screener_setup_by_id, update_screener_setup,
    delete_screener_setup,
    get_strategy_presets_by_user, get_strategy_preset_by_id, ensure_default_presets,
)
from strategy.paper_trader import paper_trader
from api.delta_client import DeltaExchangeClient
from api.market_data import get_product_by_symbol
from database.crud import get_api_credential_by_id
from config.constants import PAPER_TRADE_DEFAULT_BALANCE, PAPER_TRADE_DEFAULT_LEVERAGE
from config.settings import settings

logger = logging.getLogger(__name__)


def _lev_display(lev) -> str:
    """Format leverage for display. 0 = Max (per asset)."""
    lev = int(lev or 10)
    return "Max" if lev == 0 else f"{lev}x"

def _tw_display(tw) -> str:
    """Format time_window dict for display."""
    if not tw:
        return "24/7"
    return f"{tw['start']} → {tw['stop_entries']} → {tw['hard_exit']} IST"

# Conversation states for paper INDIVIDUAL setup creation
PAPER_NAME, PAPER_DESC, PAPER_API, PAPER_DIRECTION = range(100, 104)
PAPER_TIMEFRAME, PAPER_ASSET, PAPER_LOT_SIZE, PAPER_LEVERAGE, PAPER_PROTECTION, PAPER_CONFIRM = range(104, 110)

# Conversation states for paper SCREENER setup creation
PSCR_NAME, PSCR_DESC, PSCR_API, PSCR_ASSET_TYPE = range(110, 114)
PSCR_TIMEFRAME, PSCR_DIRECTION, PSCR_LOT_SIZE, PSCR_LEVERAGE, PSCR_PROTECTION, PSCR_CONFIRM = range(114, 120)

# Time Window states for paper individual + screener
PAPER_TIME_WINDOW, PAPER_CUSTOM_TIME = 121, 122
PSCR_TIME_WINDOW, PSCR_CUSTOM_TIME = 123, 124

# Conversation state for editable virtual balance
PAPER_SET_BALANCE_AMOUNT = 120

# Conversation states for indicator preset selection
PAPER_INDICATOR = 121
PSCR_INDICATOR = 122



# ==================== MAIN PAPER TRADING MENU ====================

async def paper_trading_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display paper trading main menu."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Get paper balance
    paper_bal = await get_paper_balance(user_id)
    balance = paper_bal["balance"] if paper_bal else PAPER_TRADE_DEFAULT_BALANCE
    balance_inr = balance * settings.usd_to_inr_rate
    locked = paper_bal.get("locked_margin", 0) if paper_bal else 0
    
    # Get paper setups (both individual and screener)
    algo_setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    scr_setups = await get_screener_setups_by_paper_mode(user_id, is_paper=True)
    total_setups = len(algo_setups) + len(scr_setups)
    active_count = (
        sum(1 for s in algo_setups if s.get("is_active", False)) +
        sum(1 for s in scr_setups if s.get("is_active", False))
    )
    
    # Get open positions
    open_positions = await get_open_paper_positions(user_id)
    
    message = (
        "**Paper Trading Hub**\n\n"
        f"**Virtual Balance:** ${balance:.2f} ({chr(8377)}{balance_inr:.2f})\n"
        f"**Locked Margin:** ${locked:.2f}\n"
        f"**Available:** ${balance - locked:.2f}\n\n"
        f"**Setups:** {total_setups} ({active_count} active)\n"
        f"**Open Positions:** {len(open_positions)}\n\n"
        "Select an option:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("+ Paper Setup", callback_data="paper_add_start"),
            InlineKeyboardButton("+ Paper Screener", callback_data="pscr_add_start"),
        ],
        [InlineKeyboardButton("View Paper Setups", callback_data="paper_view_list")],
        [InlineKeyboardButton("📄 Paper Activity", callback_data="paper_activity")],
        [InlineKeyboardButton("🧨 Close All Open Positions", callback_data="paper_close_all_confirm")],
        [InlineKeyboardButton("Delete Paper Setup", callback_data="paper_delete_list")],
        [InlineKeyboardButton("Set Virtual Balance", callback_data="paper_set_balance")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")



# ==================== CANCEL PAPER PENDING ENTRY ====================

async def paper_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending paper entry order."""
    query = update.callback_query
    await query.answer("Cancelling order...")
    
    trade_id = query.data.replace("paper_cancel_", "")
    
    from strategy.paper_trader import paper_trader
    success = await paper_trader.cancel_pending_entry(trade_id)
    
    if success:
        await query.edit_message_text(
            "✅ Pending paper order cancelled successfully.\n\n"
            "Use /start to return to main menu.",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            "❌ Failed to cancel order. It may have already been filled or cancelled.",
            parse_mode="Markdown"
        )


async def paper_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually close an open paper trade position at current market price."""
    query = update.callback_query
    await query.answer("Closing position...")
    
    trade_id = query.data.replace("paper_close_", "")
    
    from database.crud import get_trade_state_by_id, get_all_active_algo_setups, get_all_active_screener_setups, get_api_credential_by_id
    
    trade = await get_trade_state_by_id(trade_id)
    if not trade or trade.get("status") != "open":
        await query.edit_message_text(
            "❌ Trade not found or already closed.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    # Get a client for fetching live price
    client = None
    all_configs = await get_all_active_algo_setups() + await get_all_active_screener_setups()
    for config in all_configs:
        api_id = config.get("api_id")
        if api_id:
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if cred:
                client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                break
    
    if not client:
        await query.edit_message_text(
            "❌ No API credentials available to fetch live price.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    try:
        success, exit_price, _ = await paper_trader.execute_virtual_exit(
            client, trade, "Manual Close (user)"
        )
    finally:
        await client.close()
    
    if success:
        asset = trade.get("asset", "Unknown")
        direction = (trade.get("direction") or trade.get("current_position", "")).upper()
        entry_price = trade.get("entry_price", 0)
        
        await query.edit_message_text(
            f"✅ **Paper position closed**\n\n"
            f"**Asset:** {asset}\n"
            f"**Direction:** {direction}\n"
            f"**Entry:** ${entry_price:.4f}\n"
            f"**Exit:** ${exit_price:.4f}\n"
            f"**Reason:** Manual Close\n\n"
            f"Check Paper Activity for full PnL details.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Paper Activity", callback_data="paper_activity:p0")],
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ]),
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            "❌ Failed to close position. Check logs for details.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ]),
            parse_mode="Markdown"
        )


async def paper_close_all_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation dialog to close all open paper trades."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    open_positions = await get_open_paper_positions(user_id)
    
    if not open_positions:
        await query.edit_message_text(
            "ℹ️ You have no open paper positions to close.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ])
        )
        return
        
    await query.edit_message_text(
        f"⚠️ **WARNING: MASS CLOSE**\n\n"
        f"You are about to force-close **{len(open_positions)} open paper positions** at current market prices.\n"
        f"This action cannot be undone.\n\n"
        f"Are you absolutely sure?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🧨 YES, CLOSE ALL {len(open_positions)}", callback_data="paper_close_all_execute")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_paper_trading")]
        ]),
        parse_mode="Markdown"
    )

async def paper_close_all_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the mass close of all paper trades."""
    query = update.callback_query
    await query.answer("Closing all positions... This may take a moment.")
    
    user_id = str(query.from_user.id)
    open_positions = await get_open_paper_positions(user_id)
    
    if not open_positions:
        await query.edit_message_text(
            "ℹ️ No open positions found.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ])
        )
        return
        
    from database.crud import get_all_active_algo_setups, get_all_active_screener_setups, get_api_credential_by_id
    
    # Needs a client for live pricing. Just grab the first available.
    client = None
    all_configs = await get_all_active_algo_setups() + await get_all_active_screener_setups()
    for config in all_configs:
        api_id = config.get("api_id")
        if api_id:
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if cred:
                client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
                break
                
    if not client:
        await query.edit_message_text(
            "❌ No API credentials available to fetch live prices.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
            ])
        )
        return

    await query.edit_message_text("⏳ Processing mass close... Please wait.")
    
    success_count = 0
    fail_count = 0
    
    try:
        for trade in open_positions:
            success, _, _ = await paper_trader.execute_virtual_exit(
                client, trade, "Mass Manual Close (user)"
            )
            if success:
                success_count += 1
            else:
                fail_count += 1
    finally:
        await client.close()
        
    await query.edit_message_text(
        f"✅ **Mass Close Complete**\n\n"
        f"🟢 Successfully closed: {success_count}\n"
        f"🔴 Failed to close: {fail_count}\n\n"
        f"Your virtual locked margin has been freed.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")]
        ]),
        parse_mode="Markdown"
    )

# ==================== VIEW PAPER SETUPS (UNIFIED: Individual + Screener) ====================

from handlers.journal_ui import _format_indicator_params

def _group_paper_setups(all_setups: list) -> tuple:
    groups = {}
    ungrouped = []
    
    for setup in all_setups:
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
                "setups": []
            }
            
        groups[group_key]["setups"].append(setup)
        
    final_groups = {}
    final_ungrouped = []
    
    for key, data in groups.items():
        if len(data["setups"]) == 1:
            final_ungrouped.append(data["setups"][0])
        else:
            label = data["label"]
            counter = 2
            while label in final_groups:
                label = f"{data['label']} ({counter})"
                counter += 1
            final_groups[label] = data["setups"]
            
    return final_groups, final_ungrouped

async def paper_view_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View combined list of individual and screener paper setups."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    algo_setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    scr_setups = await get_screener_setups_by_paper_mode(user_id, is_paper=True)
    
    if not algo_setups and not scr_setups:
        keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("No paper trading setups found.", reply_markup=reply_markup)
        return
        
    all_setups = []
    for s in algo_setups:
        s["_internal_type"] = "algo"
        all_setups.append(s)
    for s in scr_setups:
        s["_internal_type"] = "scr"
        all_setups.append(s)
        
    final_groups, final_ungrouped = _group_paper_setups(all_setups)
    
    if "paper_setups_groups" not in context.user_data:
        context.user_data["paper_setups_groups"] = {}
    
    message = "**Paper Trading Setups**\n\n"
    keyboard = []
    
    for idx, (label, setups) in enumerate(final_groups.items()):
        grp_key = f"paper_grp_{idx}"
        context.user_data["paper_setups_groups"][grp_key] = setups
        keyboard.append([InlineKeyboardButton(f"📁 {label} ({len(setups)})", callback_data=f"paper_setup_grp_{grp_key}")])
    
    for setup in final_ungrouped:
        status = "Active" if setup.get("is_active") else "Inactive"
        if setup["_internal_type"] == "algo":
            open_trade = await get_open_trade_by_setup(str(setup["_id"]))
            position = open_trade.get("direction") if open_trade else None
            pos_text = f" | {position.upper()}" if position else ""
            
            message += (
                f"[Single] **{setup['setup_name']}**\n"
                f"  {setup['asset']} @ {setup['timeframe']} | "
                f"{_lev_display(setup.get('paper_leverage', 10))} | {status}{pos_text}\n\n"
            )
            
            keyboard.append([InlineKeyboardButton(
                f"[Single] {setup['setup_name']} - {setup['asset']}",
                callback_data=f"paper_detail_algo_{setup['_id']}"
            )])
        else:
            atype = ASSET_TYPE_TEXT_SHORT.get(setup.get("asset_selection_type", ""), "?")
            
            message += (
                f"[Screener] **{setup['setup_name']}**\n"
                f"  {atype} @ {setup.get('timeframe', '?')} | "
                f"{_lev_display(setup.get('paper_leverage', 10))} | {status}\n\n"
            )
            
            keyboard.append([InlineKeyboardButton(
                f"[Screener] {setup['setup_name']} - {atype}",
                callback_data=f"paper_detail_scr_{setup['_id']}"
            )])
    
    if not final_ungrouped and final_groups:
        message += "Select a strategy group below to view its specific timeframe and asset variations.\n"
        
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_paper_trading")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def paper_setup_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    grp_key = query.data.replace("paper_setup_grp_", "")
    
    if "paper_setups_groups" not in context.user_data or grp_key not in context.user_data["paper_setups_groups"]:
        await query.edit_message_text(
            "❌ Session expired. Please return to setups.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="paper_view_list")]])
        )
        return
        
    setups = context.user_data["paper_setups_groups"][grp_key]
    
    message = f"📁 **Strategy Group**\n\n"
    keyboard = []
    
    for setup in setups:
        status = "Active" if setup.get("is_active") else "Inactive"
        
        if setup["_internal_type"] == "algo":
            open_trade = await get_open_trade_by_setup(str(setup["_id"]))
            position = open_trade.get("direction") if open_trade else None
            pos_text = f" | {position.upper()}" if position else ""
            
            message += (
                f"[Single] **{setup['setup_name']}**\n"
                f"  {setup['asset']} @ {setup['timeframe']} | "
                f"{_lev_display(setup.get('paper_leverage', 10))} | {status}{pos_text}\n\n"
            )
            
            keyboard.append([InlineKeyboardButton(
                f"[Single] {setup['setup_name']} - {setup['asset']}",
                callback_data=f"paper_detail_algo_{setup['_id']}"
            )])
        else:
            atype = ASSET_TYPE_TEXT_SHORT.get(setup.get("asset_selection_type", ""), "?")
            
            message += (
                f"[Screener] **{setup['setup_name']}**\n"
                f"  {atype} @ {setup.get('timeframe', '?')} | "
                f"{_lev_display(setup.get('paper_leverage', 10))} | {status}\n\n"
            )
            
            keyboard.append([InlineKeyboardButton(
                f"[Screener] {setup['setup_name']} - {atype}",
                callback_data=f"paper_detail_scr_{setup['_id']}"
            )])
            
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="paper_view_list")])
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def paper_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detail of a single paper setup (individual or screener)."""
    query = update.callback_query
    await query.answer()
    
    raw = query.data.replace("paper_detail_", "")
    
    if raw.startswith("algo_"):
        setup_id = raw.replace("algo_", "")
        setup = await get_algo_setup_by_id(setup_id)
        setup_type = "algo"
    elif raw.startswith("scr_"):
        setup_id = raw.replace("scr_", "")
        setup = await get_screener_setup_by_id(setup_id)
        setup_type = "scr"
    else:
        # Legacy fallback: treat as algo
        setup_id = raw
        setup = await get_algo_setup_by_id(setup_id)
        setup_type = "algo"
    
    if not setup:
        await query.edit_message_text("Setup not found.")
        return
    
    status = "Active" if setup.get("is_active") else "Inactive"
    label = "[PAPER Single]" if setup_type == "algo" else "[PAPER Screener]"
    
    if setup_type == "algo":
        open_trade = await get_open_trade_by_setup(setup_id)
        position = open_trade.get("direction", "None") if open_trade else "None"
        entry_price = open_trade.get("entry_price") if open_trade else None
        sl_price = open_trade.get("pending_sl_price") if open_trade else None
        
        message = (
            f"**{label} {setup['setup_name']}**\n\n"
            f"**Asset:** {setup['asset']}\n"
            f"**Timeframe:** {setup['timeframe']}\n"
            f"**Direction:** {setup.get('direction', 'both').replace('_', ' ').title()}\n"
            f"**Lot Size:** {setup['lot_size']}\n"
            f"**Leverage:** {_lev_display(setup.get('paper_leverage', 10))}\n"
            f"**SL Protection:** {'Yes' if setup.get('additional_protection') else 'No'}\n"
            f"**Time Window:** {_tw_display(setup.get('time_window'))}\n"
            f"**Status:** {status}\n\n"
            f"**Current Position:** {position.upper()}\n"
        )
        
        if entry_price:
            message += f"**Entry Price:** ${entry_price:.5f}\n"
        if sl_price:
            message += f"**Stop-Loss:** ${sl_price:.5f}\n"
    else:
        atype = ASSET_TYPE_TEXT.get(setup.get("asset_selection_type", ""), "Unknown")
        
        message = (
            f"**{label} {setup['setup_name']}**\n\n"
            f"**Asset Selection:** {atype}\n"
            f"**Timeframe:** {setup.get('timeframe', '?')}\n"
            f"**Direction:** {setup.get('direction', 'both').replace('_', ' ').title()}\n"
            f"**Lot Size:** {setup.get('lot_size', 1)}\n"
            f"**Leverage:** {_lev_display(setup.get('paper_leverage', 10))}\n"
            f"**SL Protection:** {'Yes' if setup.get('additional_protection') else 'No'}\n"
            f"**Time Window:** {_tw_display(setup.get('time_window'))}\n"
            f"**Status:** {status}\n"
        )
    
    # Toggle button
    if setup.get("is_active"):
        toggle_text = "Pause Setup"
        toggle_data = f"paper_toggle_{setup_type}_{setup_id}_off"
    else:
        toggle_text = "Activate Setup"
        toggle_data = f"paper_toggle_{setup_type}_{setup_id}_on"
    
    keyboard = [
        [InlineKeyboardButton(toggle_text, callback_data=toggle_data)],
    ]
    
    # Close position button for individual setups with an open trade
    if setup_type == "algo" and open_trade and open_trade.get("status") == "open":
        trade_id = str(open_trade["_id"])
        keyboard.append([InlineKeyboardButton(
            f"🛑 Close {setup.get('asset', '')} Position",
            callback_data=f"paper_close_{trade_id}"
        )])
    
    keyboard.append([InlineKeyboardButton("Back to List", callback_data="paper_view_list")])
    keyboard.append([InlineKeyboardButton("Back to Menu", callback_data="menu_paper_trading")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def paper_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle paper setup active/inactive (individual or screener)."""
    query = update.callback_query
    await query.answer()
    
    # Format: paper_toggle_{type}_{setup_id}_{on/off}
    raw = query.data.replace("paper_toggle_", "")
    parts = raw.split("_", 2)  # type, setup_id..._action
    
    setup_type = parts[0]  # "algo" or "scr"
    rest = "_".join(parts[1:])  # setup_id_on or setup_id_off
    
    # Action is the last segment
    action = rest.rsplit("_", 1)[1]  # "on" or "off"
    setup_id = rest.rsplit("_", 1)[0]
    
    new_active = action == "on"
    
    if setup_type == "scr":
        await update_screener_setup(setup_id, {"is_active": new_active})
    else:
        await update_algo_setup(setup_id, {"is_active": new_active})
    
    status = "activated" if new_active else "paused"
    try:
        await query.edit_message_text(
            f"Paper setup has been {status}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to List", callback_data="paper_view_list")]
            ])
        )
    except Exception as e:
        logger.warning(f"Toggle edit message failed (probably already modified): {e}")


# ==================== DELETE PAPER SETUP (UNIFIED) ====================

async def paper_delete_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all paper setups (individual + screener) for deletion."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    algo_setups = await get_algo_setups_by_paper_mode(user_id, is_paper=True)
    scr_setups = await get_screener_setups_by_paper_mode(user_id, is_paper=True)
    
    if not algo_setups and not scr_setups:
        keyboard = [[InlineKeyboardButton("Back", callback_data="menu_paper_trading")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("No paper setups to delete.", reply_markup=reply_markup)
        return
    
    keyboard = []
    for setup in algo_setups:
        keyboard.append([InlineKeyboardButton(
            f"[Single] {setup['setup_name']} - {setup['asset']}",
            callback_data=f"paper_del_confirm_algo_{setup['_id']}"
        )])
    
    for setup in scr_setups:
        atype = ASSET_TYPE_TEXT_SHORT.get(setup.get("asset_selection_type", ""), "?")
        keyboard.append([InlineKeyboardButton(
            f"[Screener] {setup['setup_name']} - {atype}",
            callback_data=f"paper_del_confirm_scr_{setup['_id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_paper_trading")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Select a paper setup to delete:",
        reply_markup=reply_markup
    )


async def paper_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete a paper setup (individual or screener)."""
    query = update.callback_query
    await query.answer()
    
    raw = query.data.replace("paper_del_confirm_", "")
    user_id = str(query.from_user.id)
    
    if raw.startswith("algo_"):
        setup_id = raw.replace("algo_", "")
        await paper_trader.force_cleanup_setup(setup_id)
        success = await delete_algo_setup(setup_id, user_id)
    elif raw.startswith("scr_"):
        setup_id = raw.replace("scr_", "")
        await paper_trader.force_cleanup_setup(setup_id)
        success = await delete_screener_setup(setup_id, user_id)
    else:
        # Legacy fallback
        setup_id = raw
        await paper_trader.force_cleanup_setup(setup_id)
        success = await delete_algo_setup(setup_id, user_id)
    
    if success:
        await query.edit_message_text(
            "Paper setup deleted successfully.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
            ])
        )
    else:
        await query.edit_message_text(
            "Failed to delete paper setup.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
            ])
        )


# ==================== SET VIRTUAL BALANCE (EDITABLE) ====================

async def paper_set_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter a new virtual balance amount."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    paper_bal = await get_paper_balance(user_id)
    current = paper_bal["balance"] if paper_bal else PAPER_TRADE_DEFAULT_BALANCE
    
    await query.edit_message_text(
        f"**Set Virtual Balance**\n\n"
        f"Current balance: ${current:.2f}\n\n"
        f"Enter the new balance amount in USD (e.g., 5000 or 25000):\n\n"
        f"This will reset your performance stats.\n"
        f"Open positions will NOT be affected.\n\n"
        f"Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return PAPER_SET_BALANCE_AMOUNT


async def paper_set_balance_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new balance amount and apply it."""
    try:
        amount = float(update.message.text.strip().replace(",", "").replace("$", ""))
        if amount < 1:
            await update.message.reply_text("Balance must be at least $1. Try again:")
            return PAPER_SET_BALANCE_AMOUNT
        if amount > 10_000_000:
            await update.message.reply_text("Balance cannot exceed $10,000,000. Try again:")
            return PAPER_SET_BALANCE_AMOUNT
        
        user_id = str(update.effective_user.id)
        success = await reset_paper_balance(user_id, amount)
        
        if success:
            balance_inr = amount * settings.usd_to_inr_rate
            await update.message.reply_text(
                f"Virtual balance set to ${amount:,.2f} ({chr(8377)}{balance_inr:,.2f}).\n\n"
                f"Performance stats have been reset.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back to Paper Trading", callback_data="menu_paper_trading")]
                ])
            )
        else:
            await update.message.reply_text(
                "Failed to update balance. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back", callback_data="menu_paper_trading")]
                ])
            )
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("Invalid amount. Enter a number (e.g., 5000):")
        return PAPER_SET_BALANCE_AMOUNT





def get_paper_hub_handlers():
    from telegram.ext import MessageHandler, filters, CallbackQueryHandler, CommandHandler, ConversationHandler
    from handlers.start import main_menu_callback
    from handlers.paper_setup import cancel_paper_setup, paper_back_handler, paper_cancel_handler
    
    return [
        CallbackQueryHandler(paper_trading_menu_callback, pattern="^menu_paper_trading$"),
        CallbackQueryHandler(paper_view_list_callback, pattern="^paper_view_list$"),
        CallbackQueryHandler(paper_setup_group_callback, pattern="^paper_setup_grp_"),
        CallbackQueryHandler(paper_detail_callback, pattern="^paper_detail_"),
        CallbackQueryHandler(paper_toggle_callback, pattern="^paper_toggle_"),
        CallbackQueryHandler(paper_cancel_callback, pattern="^paper_cancel_"),
        CallbackQueryHandler(paper_close_callback, pattern="^paper_close_"),
        CallbackQueryHandler(paper_close_all_confirm_callback, pattern="^paper_close_all_confirm$"),
        CallbackQueryHandler(paper_close_all_execute_callback, pattern="^paper_close_all_execute$"),
        CallbackQueryHandler(paper_delete_list_callback, pattern="^paper_delete_list$"),
        CallbackQueryHandler(paper_delete_confirm_callback, pattern="^paper_del_confirm_"),
        ConversationHandler(
            entry_points=[CallbackQueryHandler(paper_set_balance_callback, pattern="^paper_set_balance$")],
            states={
                PAPER_SET_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_set_balance_amount_received)],
            },
            fallbacks=[
                CallbackQueryHandler(paper_back_handler, pattern="^paper_back_to_"),
                CallbackQueryHandler(paper_cancel_handler, pattern="^paper_fsm_cancel$"),
                CommandHandler("cancel", cancel_paper_setup),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
            ],
            per_message=False,
            allow_reentry=True
        )
    ]
