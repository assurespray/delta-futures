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
    Display trading activity (Real or Paper) with paginated closed trades,
    and open/pending positions at the bottom.
    """
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    
    # Parse pagination
    data = query.data
    page = 0
    base_callback = 'paper_activity' if is_paper else 'menu_algo_activity'
    
    if '_p' in data:
        parts = data.rsplit('_p', 1)
        try:
            page = int(parts[1])
        except ValueError:
            page = 0
            
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
    
    # Pre-calculate summary stats (needs all closed trades)
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
            pos_dir = activity.get('current_position') or activity.get('direction', '')
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

    # Pagination for CLOSED trades
    TRADES_PER_PAGE = 10
    total_closed = len(closed_trades)
    total_pages = max(1, (total_closed + TRADES_PER_PAGE - 1) // TRADES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * TRADES_PER_PAGE
    end_idx = min(start_idx + TRADES_PER_PAGE, total_closed)
    page_closed_trades = closed_trades[start_idx:end_idx]
    
    title_text = 'Paper' if is_paper else 'Algo'
    message = f'📜 **{title_text} Trading Activity (Last 3 Days)**\n\n'
    keyboard = []
    
    # ── CLOSED TRADES SECTION (Top) ──
    if total_closed > 0:
        if total_pages > 1:
            message += f'⚪ **CLOSED TRADES** (Page {page+1}/{total_pages})\n'
        else:
            message += f'⚪ **CLOSED TRADES** ({total_closed})\n'
            
        message += f"{'━' * 30}\n"
        
        for activity in page_closed_trades:
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
                pos_dir = activity.get('current_position') or activity.get('direction', '')
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
        
        message += '\n'
        
    # Pagination Navigation Buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton('⬅️ Prev Trades', callback_data=f'{base_callback}_p{page - 1}'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton('Older Trades ➡️', callback_data=f'{base_callback}_p{page + 1}'))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    # ── PENDING ORDERS SECTION (Middle) ──
    if pending_trades:
        message += f'⏳ **PENDING ORDERS** ({len(pending_trades)})\n'
        message += f"{'━' * 30}\n"
        
        for activity in pending_trades:
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
                keyboard.append([InlineKeyboardButton(f'❌ Cancel {asset} Pending', callback_data=f'paper_cancel_{trade_id}')])
                
        message += '\n'
    
    # ── OPEN POSITIONS SECTION (Bottom) ──
    if open_trades:
        message += f'🟢 **OPEN POSITIONS** ({len(open_trades)})\n'
        message += f"{'━' * 30}\n"
        
        for activity in open_trades:
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
                pos_dir = activity.get('current_position') or activity.get('direction', '')
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
        
        message += '\n'
    
    if not open_trades and not pending_trades and not closed_trades:
        message += 'No trades found.\n\n'
    
    # ── SUMMARY ──
    message += f"{'═' * 30}\n"
    message += f'**Summary:**\n'
    if open_trades:
        message += f'Open: {len(open_trades)}\n'
    if pending_trades:
        message += f'Pending: {len(pending_trades)}\n'
    message += f'Closed/Cancelled: {total_closed}\n'
    message += f'Winning: {winning_trades} | Losing: {losing_trades}\n'
    
    if winning_trades + losing_trades > 0:
        win_rate = (winning_trades / (winning_trades + losing_trades)) * 100
        message += f'Win Rate: {win_rate:.1f}%\n'
    
    total_pnl_emoji = '🟢' if total_pnl_usd >= 0 else '🔴'
    message += f'\n{total_pnl_emoji} **Realized PnL: ${total_pnl_usd:.2f} (₹{total_pnl_inr:.2f})**'
    
    keyboard.append([InlineKeyboardButton('🔄 Refresh', callback_data=f'{base_callback}_p{page}')])
    keyboard.append([InlineKeyboardButton('🔙 Back', callback_data=back_button_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if len(message) > 4000:
        message = message[:3997] + '...'
    
    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        if 'Message is not modified' not in str(e):
            logger.error(f'Error editing activity message: {e}')
