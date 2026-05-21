"""Algo activity (trade history) handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_trades_by_user, get_algo_setup_by_id, get_api_credential_by_id, get_screener_setup_by_id
from api.delta_client import DeltaExchangeClient
from api.positions import get_ticker_mark_price
from config.settings import settings
from datetime import datetime, timedelta
from utils.market_utils import get_contract_multiplier

logger = logging.getLogger(__name__)

def to_ist_str(dt: datetime) -> str:
    if not dt: return "N/A"
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime('%m/%d %H:%M')


async def _get_mark_price_for_open_trade(activity: dict, is_paper: bool = False) -> float:
    """Fetch current mark price from exchange for an open trade."""
    try:
        setup_id = activity.get("setup_id")
        if not setup_id:
            return 0.0
            
        # For paper trades, we can just use the public unauthenticated get_latest_price
        # if we don't have API keys, but DeltaExchange allows public ticker fetching
        # even without auth. However, we'll try to get the setup anyway.
        
        setup = await get_algo_setup_by_id(setup_id)
        if not setup:
            setup = await get_screener_setup_by_id(setup_id)
            
        if not setup:
            return 0.0
            
        api_id = setup.get("api_id")
        if not api_id:
            return 0.0
            
        cred = await get_api_credential_by_id(api_id, decrypt=True)
        if not cred:
            return 0.0
            
        client = DeltaExchangeClient(api_key=cred['api_key'], api_secret=cred['api_secret'])
        try:
            mark_price = await get_ticker_mark_price(client, activity['asset'])
            return mark_price
        finally:
            await client.close()
    except Exception as e:
        logger.warning(f"Could not fetch mark price for activity display: {e}")
        return 0.0


async def algo_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Algo Activity button click."""
    await _render_activity(update, context, is_paper=False)

async def paper_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Paper Activity button click."""
    await _render_activity(update, context, is_paper=True)

async def _render_activity(update: Update, context: ContextTypes.DEFAULT_TYPE, is_paper: bool):
    """
    Display trading activity (Real or Paper) with tabbed navigation and pagination.
    Tabs: [ Closed | Open | Pending ]
    """
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Parse pagination and tab
    data = query.data
    page = 0
    tab = "auto"
    base_callback = 'paper_activity' if is_paper else 'menu_algo_activity'
    
    if "_p" in data:
        parts = data.rsplit("_p", 1)
        try:
            page = int(parts[1])
        except ValueError:
            page = 0
        data_before_p = parts[0]
    else:
        data_before_p = data
        
    if data_before_p.endswith("_open"):
        tab = "open"
    elif data_before_p.endswith("_pending"):
        tab = "pending"
    elif data_before_p.endswith("_closed"):
        tab = "closed"
        
    # Get last 3 days of activity
    activities = await get_trades_by_user(user_id, days=3, is_paper=is_paper)
    
    back_button_data = 'menu_paper_trading' if is_paper else 'main_menu'
    
    if not activities:
        keyboard = [[InlineKeyboardButton('🔙 Back', callback_data=back_button_data)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        mode_text = 'paper' if is_paper else 'algo'
        await query.edit_message_text(
            f'ℹ️ No {mode_text} trading activity in the last 3 days.',
            reply_markup=reply_markup
        )
        return
    
    # Separate into categories
    open_trades = [a for a in activities if a.get('status') == 'open']
    pending_trades = [a for a in activities if a.get('status') == 'pending_entry']
    closed_trades = [a for a in activities if a.get('status') in ('closed', 'cancelled')]
    
    # Auto-select tab if not specified
    if tab == "auto":
        if open_trades:
            tab = "open"
        elif pending_trades:
            tab = "pending"
        else:
            tab = "closed"
            
    # Ensure tab is valid if a category becomes empty
    if tab == "open" and not open_trades:
        tab = "closed" if closed_trades else "pending"
    if tab == "pending" and not pending_trades:
        tab = "open" if open_trades else "closed"
    if tab == "closed" and not closed_trades:
        tab = "open" if open_trades else "pending"
    
    # Pre-calculate summary stats
    total_pnl_usd = 0.0
    total_pnl_inr = 0.0
    winning_trades = 0
    losing_trades = 0
    
    for activity in closed_trades:
        lot_size = activity.get('lot_size', 0)
        entry_price = activity.get('entry_price') or activity.get('last_entry_price') or activity.get('entry_trigger_price')
        exit_price = activity.get('exit_price', 0)
        
        pnl = 0.0
        pnl_inr = 0.0
        if entry_price and exit_price:
            contract_multiplier = get_contract_multiplier(activity.get('asset', ''))
            pos_dir = activity.get('direction') or activity.get('current_position', '')
            if pos_dir == 'long':
                pnl = (exit_price - entry_price) * lot_size * contract_multiplier
            elif pos_dir == 'short':
                pnl = (entry_price - exit_price) * lot_size * contract_multiplier
            from config.settings import settings as app_settings
            pnl_inr = pnl * app_settings.usd_to_inr_rate

        if pnl >= 0:
            winning_trades += 1
        else:
            losing_trades += 1
        
        total_pnl_usd += pnl
        total_pnl_inr += pnl_inr

    # Setup active items for pagination
    ITEMS_PER_PAGE = 5 if tab in ["open", "pending"] else 10
    
    if tab == "open":
        items = open_trades
        tab_title = f"🟢 **OPEN POSITIONS** ({len(open_trades)})"
    elif tab == "pending":
        items = pending_trades
        tab_title = f"⏳ **PENDING ORDERS** ({len(pending_trades)})"
    else:
        items = closed_trades
        tab_title = f"⚪ **CLOSED TRADES** ({len(closed_trades)})"
        
    total_items = len(items)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total_items)
    page_items = items[start_idx:end_idx]
    
    title_text = 'Paper' if is_paper else 'Algo'
    message = f'📜 **{title_text} Trading Activity (Last 3 Days)**\n\n'
    
    # Tab Title
    if total_pages > 1:
        message += f'{tab_title} - Page {page+1}/{total_pages}\n'
    else:
        message += f'{tab_title}\n'
    message += f"{'━' * 30}\n"
    
    keyboard = []
    
    # ── TAB RENDERING ──
    if tab == "closed":
        for activity in page_items:
            setup_name = activity['setup_name']
            asset = activity['asset']
            direction = activity.get('direction', '').upper()
            lot_size = activity.get('lot_size', 0)
            entry_price = activity.get('entry_price') or activity.get('last_entry_price') or activity.get('entry_trigger_price')
            entry_time = activity.get('entry_time')
            
            entry_price_str = f'${entry_price:.4f}' if entry_price else 'N/A'
            entry_time_str = to_ist_str(entry_time)
            
            exit_price = activity.get('exit_price', 0)
            exit_time = activity.get('exit_time')
            
            pnl = 0.0
            pnl_inr = 0.0
            if entry_price and exit_price:
                contract_multiplier = get_contract_multiplier(asset)
                pos_dir = activity.get('direction') or activity.get('current_position', '')
                if pos_dir == 'long':
                    pnl = (exit_price - entry_price) * lot_size * contract_multiplier
                elif pos_dir == 'short':
                    pnl = (entry_price - exit_price) * lot_size * contract_multiplier
                from config.settings import settings as app_settings
                pnl_inr = pnl * app_settings.usd_to_inr_rate

            pnl_emoji = '🟢' if pnl >= 0 else '🔴'
            exit_price_str = f'${exit_price:.4f}' if exit_price else 'N/A'
            exit_time_str = to_ist_str(exit_time)
            
            message += f'\n📊 **{setup_name}** - {asset}\n'
            message += f'Direction: {direction} | Size: {lot_size}\n'
            message += f'🔵 Entry: {entry_price_str} | {entry_time_str}\n'
            message += f'🔴 Exit: {exit_price_str} | {exit_time_str}\n'
            message += f'{pnl_emoji} PnL: ${pnl:.2f} (₹{pnl_inr:.2f})\n'

    elif tab == "open":
        for activity in page_items:
            setup_name = activity['setup_name']
            asset = activity['asset']
            direction = activity.get('direction', '').upper()
            lot_size = activity.get('lot_size', 0)
            entry_price = activity.get('entry_price') or activity.get('last_entry_price') or activity.get('entry_trigger_price')
            entry_time = activity.get('entry_time')
            
            entry_price_str = f'${entry_price:.4f}' if entry_price else 'N/A'
            entry_time_str = to_ist_str(entry_time)
            
            mark_price = await _get_mark_price_for_open_trade(activity, is_paper)
            sl_price = activity.get('pending_sl_price')
            
            upnl = 0.0
            upnl_inr = 0.0
            upnl_str = 'N/A'
            if entry_price and mark_price:
                pos_dir = activity.get('direction') or activity.get('current_position', '')
                contract_multiplier = get_contract_multiplier(asset)
                if pos_dir == 'long':
                    upnl = (mark_price - entry_price) * lot_size * contract_multiplier
                elif pos_dir == 'short':
                    upnl = (entry_price - mark_price) * lot_size * contract_multiplier
                from config.settings import settings as app_settings
                upnl_inr = upnl * app_settings.usd_to_inr_rate
                upnl_emoji = '🟢' if upnl >= 0 else '🔴'
                upnl_str = f'{upnl_emoji} ${upnl:.2f} (₹{upnl_inr:.2f})'
            
            message += f'\n📊 **{setup_name}** - {asset}\n'
            message += f'Direction: {direction} | Size: {lot_size}\n'
            message += f'🔵 Entry: {entry_price_str} | {entry_time_str}\n'
            if mark_price:
                message += f'📈 Mark: ${mark_price:.4f}\n'
            if sl_price:
                message += f'🛡️ SL: ${sl_price:.4f}\n'
            message += f'💰 Unrealized PnL: {upnl_str}\n'
            
            # Manual close button for paper trades
            if is_paper:
                trade_id = str(activity['_id'])
                keyboard.append([InlineKeyboardButton(f'🛑 Close {asset} ({setup_name})', callback_data=f'paper_close_{trade_id}')])

    elif tab == "pending":
        for activity in page_items:
            setup_name = activity['setup_name']
            asset = activity['asset']
            direction = activity.get('pending_entry_side', activity.get('direction', '')).upper()
            lot_size = activity.get('lot_size', 0)
            trigger_price = activity.get('entry_trigger_price')
            
            trigger_str = f'${trigger_price:.5f}' if trigger_price else 'N/A'
            sl_price = activity.get('pending_sl_price')
            
            message += f'\n📊 **{setup_name}** - {asset}\n'
            message += f'Direction: {direction} | Size: {lot_size}\n'
            message += f'🎯 Trigger: {trigger_str}\n'
            if sl_price:
                message += f'🛡️ SL: ${sl_price:.4f}\n'
                
            if is_paper:
                trade_id = str(activity['_id'])
                keyboard.append([InlineKeyboardButton(f'❌ Cancel {asset} ({setup_name})', callback_data=f'paper_cancel_{trade_id}')])
                
    message += '\n'
    
    # ── PAGINATION NAV ──
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton('⬅️ Prev', callback_data=f'{base_callback}_{tab}_p{page - 1}'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton('Next ➡️', callback_data=f'{base_callback}_{tab}_p{page + 1}'))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    # ── TABS NAV ──
    tabs_row = []
    if open_trades:
        text = f"🟢 Open ({len(open_trades)})" if tab != "open" else f"📍 Open"
        tabs_row.append(InlineKeyboardButton(text, callback_data=f"{base_callback}_open_p0"))
    if pending_trades:
        text = f"⏳ Pending ({len(pending_trades)})" if tab != "pending" else f"📍 Pending"
        tabs_row.append(InlineKeyboardButton(text, callback_data=f"{base_callback}_pending_p0"))
    if closed_trades:
        text = f"⚪ Closed ({len(closed_trades)})" if tab != "closed" else f"📍 Closed"
        tabs_row.append(InlineKeyboardButton(text, callback_data=f"{base_callback}_closed_p0"))
        
    if tabs_row:
        # If there are many tabs, might want to split or keep in one row. Max 3 is fine for one row.
        keyboard.append(tabs_row)
        
    keyboard.append([
        InlineKeyboardButton('🔄 Refresh', callback_data=f'{base_callback}_{tab}_p{page}'),
        InlineKeyboardButton('🔙 Back to Menu', callback_data=back_button_data)
    ])
    
    # ── SUMMARY ──
    message += f"{'═' * 30}\n"
    message += f'**Summary (Last 3 Days):**\n'
    if open_trades:
        message += f'Open: {len(open_trades)}\n'
    if pending_trades:
        message += f'Pending: {len(pending_trades)}\n'
    message += f'Closed/Cancelled: {len(closed_trades)}\n'
    message += f'Winning: {winning_trades} | Losing: {losing_trades}\n'
    
    if winning_trades + losing_trades > 0:
        win_rate = (winning_trades / (winning_trades + losing_trades)) * 100
        message += f'Win Rate: {win_rate:.1f}%\n'
    
    pnl_emoji = '🟢' if total_pnl_usd >= 0 else '🔴'
    message += f'Total PnL: {pnl_emoji} ${total_pnl_usd:.2f} (₹{total_pnl_inr:.2f})\n'

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        if 'Message is not modified' not in str(e):
            logger.warning(f"Edit message failed in activity: {e}")

