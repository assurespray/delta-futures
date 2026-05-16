import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_indicator_cache_by_type
from datetime import datetime

logger = logging.getLogger(__name__)

ASSETS_PER_PAGE = 8  # ~8 assets safe with display_details (5-8 lines each)


async def tracker_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu for the Live Indicator Tracker."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📊 Real - Individual", callback_data="tracker_sub_real_algo")],
        [InlineKeyboardButton("📊 Real - Screener", callback_data="tracker_sub_real_screener")],
        [InlineKeyboardButton("🎮 Paper - Individual", callback_data="tracker_sub_paper_algo")],
        [InlineKeyboardButton("🎮 Paper - Screener", callback_data="tracker_sub_paper_screener")],
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

    calc_time = c["calculated_at"]
    age_sec = (datetime.utcnow() - calc_time).total_seconds()
    age_str = f"{int(age_sec)}s ago" if age_sec < 60 else f"{int(age_sec // 60)}m ago"

    line = f"  • **{asset}** ({tf}) - ${price:.4f} `[{age_str}]`\n"

    # Use display_details if available (dynamic per-strategy details)
    details = c.get("display_details")
    if details:
        items = list(details.items())
        for i, (key, val) in enumerate(items):
            connector = "└" if i == len(items) - 1 else "├"
            if isinstance(val, float):
                # Auto-detect decimal places from price magnitude
                if val >= 100:
                    formatted = f"${val:.2f}"
                elif val >= 1:
                    formatted = f"${val:.4f}"
                elif val > 0:
                    formatted = f"${val:.6f}"
                else:
                    formatted = f"${val:.4f}"
                line += f"    {connector} {key}: {formatted}\n"
            else:
                line += f"    {connector} {key}: {val}\n"
    else:
        # Fallback: legacy primary/secondary format
        p_sig_val = c.get("primary_signal", 0)
        s_sig_val = c.get("secondary_signal", 0)
        p_val = c.get("primary_value", 0.0)
        s_val = c.get("secondary_value", 0.0)
        p_name = c.get("primary_name", "Primary")
        s_name = c.get("secondary_name", "Secondary")

        p_sig = "🔵 UP" if p_sig_val == 1 else ("🔴 DOWN" if p_sig_val == -1 else "⚪ NEUTRAL")
        s_sig = "🔵 UP" if s_sig_val == 1 else ("🔴 DOWN" if s_sig_val == -1 else "⚪ NEUTRAL")

        if p_val == s_val and p_sig_val == s_sig_val:
            line += f"    └ Signal: {p_sig} (${p_val:.4f})\n"
        else:
            line += f"    ├ {p_name}: {p_sig} (${p_val:.4f})\n"
            line += f"    └ {s_name}: {s_sig} (${s_val:.4f})\n"
    return line


async def _render_tracker_submenu(query, data: str, context: ContextTypes.DEFAULT_TYPE):
    """Show a sub-menu listing individual setups as buttons, plus an 'All' option."""
    await query.answer()

    # data: "tracker_sub_real_algo" or "tracker_sub_paper_screener"
    parts = data.split("_")  # ['tracker', 'sub', 'real'/'paper', 'algo'/'screener']
    mode_str = parts[2]  # real / paper
    type_str = parts[3]  # algo / screener
    is_paper = (mode_str == "paper")

    mode_label = "🎮 Paper" if is_paper else "📊 Real"
    type_label = "Individual" if type_str == "algo" else "Screener"
    title = f"{mode_label} - {type_label}"

    # Fetch active caches and extract unique setup names
    caches = await get_indicator_cache_by_type(type_str, is_paper)
    setup_names = sorted(set(c["setup_name"] for c in caches)) if caches else []

    # Store setup names in user_data for index-based retrieval
    context.user_data["tracker_setup_names"] = setup_names

    base_view = f"tracker_{mode_str}_{type_str}"

    if not setup_names:
        keyboard = [[InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"ℹ️ No active scans found for **{title}** in the last hour.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    keyboard = []
    # "View All" button at the top
    keyboard.append([InlineKeyboardButton(f"🌟 All Setups ({len(setup_names)})", callback_data=f"{base_view}_all:p0")])

    # Individual setup buttons — 1 per row
    for idx, name in enumerate(setup_names):
        # Count assets in this setup for the label
        asset_count = sum(1 for c in caches if c["setup_name"] == name)
        label = f"📁 {name} ({asset_count})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{base_view}_idx:{idx}:p0")])

    keyboard.append([InlineKeyboardButton("🔙 Back to Tracker Menu", callback_data="menu_indicator_tracker")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"**🔍 {title}**\n\n"
        f"Found **{len(setup_names)}** active setup(s).\n"
        "Select a setup to view its indicators, or view all at once.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def _render_tracker_view(query, title: str, setup_type: str, is_paper: bool,
                               page: int = 0, filter_setup_name: str = None,
                               base_cmd: str = None):
    """Helper to render a paginated list of tracked assets, optionally filtered to one setup."""
    await query.answer("Fetching live indicators...")

    caches = await get_indicator_cache_by_type(setup_type, is_paper)

    # Apply setup filter if specified
    if filter_setup_name and caches:
        caches = [c for c in caches if c["setup_name"] == filter_setup_name]

    # Build back button target — go to the sub-menu for this category
    mode_str = "paper" if is_paper else "real"
    sub_menu_data = f"tracker_sub_{mode_str}_{setup_type}"

    if not caches:
        display_title = f"{title} — {filter_setup_name}" if filter_setup_name else title
        keyboard = [[InlineKeyboardButton("🔙 Back to Setups", callback_data=sub_menu_data)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"ℹ️ No active scans found for **{display_title}** in the last hour.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    all_entries = list(caches)
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

    display_title = f"{title} — {filter_setup_name}" if filter_setup_name else title
    if total_pages > 1:
        message = f"**{display_title} Dashboard** (Page {page + 1}/{total_pages})\n"
        message += f"Showing {start + 1}-{end} of {total_assets} assets\n\n"
    else:
        message = f"**{display_title} Dashboard**\n\n"

    for setup_name, assets in setups.items():
        message += f"📁 **Setup:** `{setup_name}`\n"
        for c in assets:
            message += _format_asset_line(c)
        message += "\n"

    # Build navigation buttons — use base_cmd so pagination stays in the same filtered view
    if not base_cmd:
        base_cmd = f"tracker_{mode_str}_{setup_type}"
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{base_cmd}:p{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{base_cmd}:p{page + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"{base_cmd}:p{page}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Setups", callback_data=sub_menu_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Hard truncation failsafe — Telegram rejects messages > 4096 chars
    if len(message) > 4000:
        message = message[:3997] + "…"
        logger.warning(f"Tracker message truncated: {len(message)} chars (page {page+1})")

    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.error(f"Tracker edit_message_text failed: {e}")
            raise


async def tracker_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route the specific tracker view request (with optional pagination)."""
    query = update.callback_query
    data = query.data

    # ---- Sub-menu: list individual setups as buttons ----
    if data.startswith("tracker_sub_"):
        await _render_tracker_submenu(query, data, context)
        return

    # ---- Parse page from callback_data: e.g. "tracker_real_algo_all:p2" ----
    page = 0
    if ":p" in data:
        parts = data.rsplit(":p", 1)
        base = parts[0]
        try:
            page = int(parts[1])
        except (ValueError, IndexError):
            page = 0
    else:
        base = data

    # ---- Determine mode/type and filter ----
    filter_setup_name = None
    base_cmd = base  # preserve full base for pagination callbacks

    # Handle "_idx:N" — specific setup by index
    if "_idx:" in base:
        idx_parts = base.split("_idx:")
        core = idx_parts[0]  # e.g. "tracker_real_algo"
        try:
            idx = int(idx_parts[1])
            names = context.user_data.get("tracker_setup_names", [])
            if 0 <= idx < len(names):
                filter_setup_name = names[idx]
            else:
                logger.warning(f"Tracker idx {idx} out of range ({len(names)} names)")
        except (ValueError, IndexError):
            pass
    # Handle "_all" — show everything
    elif base.endswith("_all"):
        core = base[:-4]  # strip "_all"
        base_cmd = base  # keep "_all" in pagination callbacks
    else:
        core = base

    # Map core to (title, setup_type, is_paper)
    route_map = {
        "tracker_real_algo":      ("📊 Real - Individual", "algo", False),
        "tracker_real_screener":  ("📊 Real - Screener", "screener", False),
        "tracker_paper_algo":     ("🎮 Paper - Individual", "algo", True),
        "tracker_paper_screener": ("🎮 Paper - Screener", "screener", True),
    }

    route = route_map.get(core)
    if not route:
        await query.answer("Invalid tracker request.", show_alert=True)
        return

    title, setup_type, is_paper = route
    await _render_tracker_view(
        query, title, setup_type, is_paper,
        page=page,
        filter_setup_name=filter_setup_name,
        base_cmd=base_cmd
    )
