import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_indicator_cache_by_type
from datetime import datetime

logger = logging.getLogger(__name__)

ASSETS_PER_PAGE = 25  # ~25 assets fit comfortably in 4096 chars


async def tracker_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu for the Live Indicator Tracker."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📊 Real - Individual", callback_data="tracker_real_algo")],
        [InlineKeyboardButton("📊 Real - Screener", callback_data="tracker_real_screener")],
        [InlineKeyboardButton("🎮 Paper - Individual", callback_data="tracker_paper_algo")],
        [InlineKeyboardButton("🎮 Paper - Screener", callback_data="tracker_paper_screener")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "**🔍 Live Indicator Tracker**\n\n"
        "View exactly which assets the bot is scanning right now, "
        "along with their live prices and SuperTrend indicators.\n\n"
        "Select a category below to view the latest calculations.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


def _format_asset_line(c: dict) -> str:
    """Format a single asset cache entry into display text."""
    asset = c["asset"]
    tf = c["timeframe"]
    price = c.get("current_price", 0.0)

    p_sig_val = c.get("primary_signal", c.get("perusu_signal", 0))
    s_sig_val = c.get("secondary_signal", c.get("sirusu_signal", 0))
    p_val = c.get("primary_value", c.get("perusu_value", 0.0))
    s_val = c.get("secondary_value", c.get("sirusu_value", 0.0))
    p_name = c.get("primary_name", "Primary")
    s_name = c.get("secondary_name", "Secondary")

    p_sig = "🔵 UP" if p_sig_val == 1 else ("🔴 DOWN" if p_sig_val == -1 else "⚪ NEUTRAL")
    s_sig = "🔵 UP" if s_sig_val == 1 else ("🔴 DOWN" if s_sig_val == -1 else "⚪ NEUTRAL")

    calc_time = c["calculated_at"]
    age_sec = (datetime.utcnow() - calc_time).total_seconds()
    age_str = f"{int(age_sec)}s ago" if age_sec < 60 else f"{int(age_sec // 60)}m ago"

    line = f"  • **{asset}** ({tf}) - ${price:.4f} `[{age_str}]`\n"
    if p_val == s_val and p_sig_val == s_sig_val:
        line += f"    └ Signal: {p_sig} (${p_val:.4f})\n"
    else:
        line += f"    ├ {p_name}: {p_sig} (${p_val:.4f})\n"
        line += f"    └ {s_name}: {s_sig} (${s_val:.4f})\n"
    return line


async def _render_tracker_view(query, title: str, setup_type: str, is_paper: bool, page: int = 0):
    """Helper to render a paginated list of tracked assets."""
    await query.answer("Fetching live indicators...")

    caches = await get_indicator_cache_by_type(setup_type, is_paper)

    if not caches:
        keyboard = [[InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"ℹ️ No active scans found for **{title}** in the last hour.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    # Build a flat list of (setup_name, cache_entry) preserving setup grouping
    all_entries = []
    for c in caches:
        all_entries.append(c)

    total_assets = len(all_entries)
    total_pages = max(1, (total_assets + ASSETS_PER_PAGE - 1) // ASSETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ASSETS_PER_PAGE
    end = min(start + ASSETS_PER_PAGE, total_assets)
    page_entries = all_entries[start:end]

    # Group page entries by setup
    setups = {}
    for c in page_entries:
        setup_name = c["setup_name"]
        if setup_name not in setups:
            setups[setup_name] = []
        setups[setup_name].append(c)

    if total_pages > 1:
        message = f"**{title} Dashboard** (Page {page + 1}/{total_pages})\n"
        message += f"Showing {start + 1}-{end} of {total_assets} assets\n\n"
    else:
        message = f"**{title} Dashboard**\n\n"

    for setup_name, assets in setups.items():
        message += f"📁 **Setup:** `{setup_name}`\n"
        for c in assets:
            message += _format_asset_line(c)
        message += "\n"

    # Build navigation buttons
    base_data = f"tracker_{'paper' if is_paper else 'real'}_{setup_type}"
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{base_data}_p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{base_data}_p{page + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"{base_data}_p{page}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise


async def tracker_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route the specific tracker view request (with optional pagination)."""
    query = update.callback_query
    data = query.data

    # Parse page from callback_data: e.g. "tracker_paper_screener_p3"
    page = 0
    if "_p" in data:
        parts = data.rsplit("_p", 1)
        base = parts[0]
        try:
            page = int(parts[1])
        except (ValueError, IndexError):
            page = 0
    else:
        base = data

    if base == "tracker_real_algo":
        await _render_tracker_view(query, "📊 Real - Individual", "algo", False, page)
    elif base == "tracker_real_screener":
        await _render_tracker_view(query, "📊 Real - Screener", "screener", False, page)
    elif base == "tracker_paper_algo":
        await _render_tracker_view(query, "🎮 Paper - Individual", "algo", True, page)
    elif base == "tracker_paper_screener":
        await _render_tracker_view(query, "🎮 Paper - Screener", "screener", True, page)
