"""
Performance Dashboard Telegram Handler.

Fully modular: delete this file and remove references in bot.py
and start.py to completely remove performance tracking UI.

Features:
- Separate Real vs Paper trade performance views
- Equity curve chart generation (matplotlib)
- Win rate, PnL, trade count metrics
- CSV download of trade history
"""
import io
import csv
import logging
from datetime import datetime
from typing import List, Dict, Any

import matplotlib
matplotlib.use('Agg')  # Headless mode - prevents crash on servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import (
    get_paper_balance, get_paper_trade_activities,
    get_real_trade_activities
)
from config.settings import settings
from config.constants import PAPER_TRADE_DEFAULT_BALANCE

logger = logging.getLogger(__name__)


# ==================== MAIN PERFORMANCE MENU ====================

async def performance_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display performance menu with Real vs Paper options."""
    query = update.callback_query
    await query.answer()
    
    message = (
        "**Performance Dashboard**\n\n"
        "View trading performance metrics, equity curves,\n"
        "and download trade history.\n\n"
        "Select which trades to analyze:"
    )
    
    keyboard = [
        [InlineKeyboardButton("Real Trade Performance", callback_data="perf_real")],
        [InlineKeyboardButton("Paper Trade Performance", callback_data="perf_paper")],
        [InlineKeyboardButton("Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /performance command."""
    message = (
        "**Performance Dashboard**\n\n"
        "View trading performance metrics, equity curves,\n"
        "and download trade history.\n\n"
        "Select which trades to analyze:"
    )
    
    keyboard = [
        [InlineKeyboardButton("Real Trade Performance", callback_data="perf_real")],
        [InlineKeyboardButton("Paper Trade Performance", callback_data="perf_paper")],
        [InlineKeyboardButton("Back to Main Menu", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# ==================== REAL TRADE PERFORMANCE ====================

async def perf_real_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display real trading performance."""
    query = update.callback_query
    await query.answer("Calculating...")
    
    user_id = str(query.from_user.id)
    activities = await get_real_trade_activities(user_id, closed_only=True)
    
    if not activities:
        keyboard = [
            [InlineKeyboardButton("Back", callback_data="menu_performance")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "No closed real trades found.",
            reply_markup=reply_markup
        )
        return
    
    message = _build_performance_message("Real Trading", activities)
    
    keyboard = [
        [InlineKeyboardButton("Equity Curve Chart", callback_data="perf_real_chart")],
        [InlineKeyboardButton("Download CSV", callback_data="perf_real_csv")],
        [InlineKeyboardButton("Back", callback_data="menu_performance")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def perf_real_chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send real trading equity curve chart."""
    query = update.callback_query
    await query.answer("Generating chart...")
    
    user_id = str(query.from_user.id)
    activities = await get_real_trade_activities(user_id, closed_only=True)
    
    if not activities:
        await query.edit_message_text("No closed real trades to chart.")
        return
    
    # Generate chart
    chart_buf = _generate_equity_chart("Real Trading", activities, starting_balance=None)
    
    if chart_buf:
        await query.message.reply_photo(
            photo=chart_buf,
            caption="Real Trading - Equity Curve (Cumulative PnL)"
        )
    else:
        await query.message.reply_text("Failed to generate chart.")


async def perf_real_csv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send real trading CSV file."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    
    user_id = str(query.from_user.id)
    activities = await get_real_trade_activities(user_id)
    
    if not activities:
        await query.edit_message_text("No real trades to export.")
        return
    
    csv_buf = _generate_csv(activities, "real")
    
    if csv_buf:
        filename = f"real_trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        await query.message.reply_document(
            document=csv_buf,
            filename=filename,
            caption=f"Real Trading History - {len(activities)} trades"
        )
    else:
        await query.message.reply_text("Failed to generate CSV.")


# ==================== PAPER TRADE PERFORMANCE ====================

async def perf_paper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display paper trading performance."""
    query = update.callback_query
    await query.answer("Calculating...")
    
    user_id = str(query.from_user.id)
    activities = await get_paper_trade_activities(user_id, closed_only=True)
    paper_bal = await get_paper_balance(user_id)
    
    if not activities:
        balance = paper_bal["balance"] if paper_bal else PAPER_TRADE_DEFAULT_BALANCE
        keyboard = [
            [InlineKeyboardButton("Back", callback_data="menu_performance")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"No closed paper trades found.\n\n"
            f"Virtual Balance: ${balance:.2f}",
            reply_markup=reply_markup
        )
        return
    
    message = _build_performance_message("Paper Trading", activities, paper_bal)
    
    keyboard = [
        [InlineKeyboardButton("Equity Curve Chart", callback_data="perf_paper_chart")],
        [InlineKeyboardButton("PnL Per Trade Chart", callback_data="perf_paper_pnl_chart")],
        [InlineKeyboardButton("Download CSV", callback_data="perf_paper_csv")],
        [InlineKeyboardButton("Back", callback_data="menu_performance")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def perf_paper_chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send paper trading equity curve chart."""
    query = update.callback_query
    await query.answer("Generating chart...")
    
    user_id = str(query.from_user.id)
    activities = await get_paper_trade_activities(user_id, closed_only=True)
    paper_bal = await get_paper_balance(user_id)
    
    if not activities:
        await query.edit_message_text("No closed paper trades to chart.")
        return
    
    starting_balance = paper_bal.get("initial_balance", PAPER_TRADE_DEFAULT_BALANCE) if paper_bal else PAPER_TRADE_DEFAULT_BALANCE
    chart_buf = _generate_equity_chart("Paper Trading", activities, starting_balance)
    
    if chart_buf:
        await query.message.reply_photo(
            photo=chart_buf,
            caption="Paper Trading - Equity Curve"
        )
    else:
        await query.message.reply_text("Failed to generate chart.")


async def perf_paper_pnl_chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send per-trade PnL bar chart."""
    query = update.callback_query
    await query.answer("Generating chart...")
    
    user_id = str(query.from_user.id)
    activities = await get_paper_trade_activities(user_id, closed_only=True)
    
    if not activities:
        await query.edit_message_text("No closed paper trades to chart.")
        return
    
    chart_buf = _generate_pnl_bar_chart("Paper Trading", activities)
    
    if chart_buf:
        await query.message.reply_photo(
            photo=chart_buf,
            caption="Paper Trading - PnL Per Trade"
        )
    else:
        await query.message.reply_text("Failed to generate chart.")


async def perf_paper_csv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send paper trading CSV file."""
    query = update.callback_query
    await query.answer("Generating CSV...")
    
    user_id = str(query.from_user.id)
    activities = await get_paper_trade_activities(user_id)
    
    if not activities:
        await query.edit_message_text("No paper trades to export.")
        return
    
    csv_buf = _generate_csv(activities, "paper")
    
    if csv_buf:
        filename = f"paper_trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        await query.message.reply_document(
            document=csv_buf,
            filename=filename,
            caption=f"Paper Trading History - {len(activities)} trades"
        )
    else:
        await query.message.reply_text("Failed to generate CSV.")


# ==================== HELPER FUNCTIONS ====================

def _build_performance_message(
    title: str,
    activities: List[Dict[str, Any]],
    paper_bal: Dict[str, Any] = None
) -> str:
    """Build performance summary message."""
    
    total_trades = len(activities)
    winning = sum(1 for a in activities if (a.get("pnl") or 0) > 0)
    losing = sum(1 for a in activities if (a.get("pnl") or 0) <= 0)
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
    
    total_pnl = sum(a.get("pnl", 0) or 0 for a in activities)
    total_pnl_inr = total_pnl * settings.usd_to_inr_rate
    total_fees = sum(a.get("paper_fees", 0) or 0 for a in activities)
    
    # Best and worst trades
    pnls = [a.get("pnl", 0) or 0 for a in activities]
    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0
    avg_pnl = (total_pnl / total_trades) if total_trades > 0 else 0
    
    # Win/Loss streaks
    max_win_streak = _calculate_streak(pnls, positive=True)
    max_loss_streak = _calculate_streak(pnls, positive=False)
    
    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
    
    pnl_emoji = "+" if total_pnl >= 0 else ""
    
    message = f"**{title} Performance**\n\n"
    
    if paper_bal:
        balance = paper_bal.get("balance", PAPER_TRADE_DEFAULT_BALANCE)
        initial = paper_bal.get("initial_balance", PAPER_TRADE_DEFAULT_BALANCE)
        roi = ((balance - initial) / initial * 100) if initial > 0 else 0
        message += (
            f"**Virtual Balance:** ${balance:.2f}\n"
            f"**Initial Balance:** ${initial:.2f}\n"
            f"**ROI:** {roi:+.2f}%\n\n"
        )
    
    message += (
        f"{'=' * 28}\n"
        f"**Trades:** {total_trades} | W: {winning} | L: {losing}\n"
        f"**Win Rate:** {win_rate:.1f}%\n"
        f"**Profit Factor:** {profit_factor:.2f}\n\n"
        f"**Total PnL:** {pnl_emoji}${total_pnl:.2f} ({chr(8377)}{total_pnl_inr:.2f})\n"
        f"**Avg PnL/Trade:** ${avg_pnl:.4f}\n"
        f"**Best Trade:** ${best_trade:.4f}\n"
        f"**Worst Trade:** ${worst_trade:.4f}\n\n"
        f"**Max Win Streak:** {max_win_streak}\n"
        f"**Max Loss Streak:** {max_loss_streak}\n"
    )
    
    if total_fees > 0:
        message += f"**Total Fees:** ${total_fees:.4f}\n"
    
    # Per-asset breakdown
    assets = {}
    for a in activities:
        asset = a.get("asset", "Unknown")
        if asset not in assets:
            assets[asset] = {"trades": 0, "pnl": 0.0}
        assets[asset]["trades"] += 1
        assets[asset]["pnl"] += a.get("pnl", 0) or 0
    
    if len(assets) > 1:
        message += f"\n{'=' * 28}\n**Per Asset:**\n"
        for asset, data in sorted(assets.items(), key=lambda x: x[1]["pnl"], reverse=True):
            p = data["pnl"]
            message += f"  {asset}: {data['trades']} trades | ${p:+.4f}\n"
    
    return message


def _calculate_streak(pnls: List[float], positive: bool = True) -> int:
    """Calculate max consecutive win or loss streak."""
    max_streak = 0
    current = 0
    for p in pnls:
        if (positive and p > 0) or (not positive and p <= 0):
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _generate_equity_chart(
    title: str,
    activities: List[Dict[str, Any]],
    starting_balance: float = None
) -> io.BytesIO:
    """Generate equity curve chart as PNG bytes."""
    try:
        # Sort by exit time
        sorted_acts = sorted(
            [a for a in activities if a.get("exit_time")],
            key=lambda x: x["exit_time"]
        )
        
        if not sorted_acts:
            return None
        
        dates = []
        equity = []
        current_bal = starting_balance if starting_balance else 0
        
        for act in sorted_acts:
            pnl = act.get("pnl", 0) or 0
            current_bal += pnl
            
            exit_time = act.get("exit_time")
            if isinstance(exit_time, str):
                exit_time = datetime.fromisoformat(exit_time)
            
            dates.append(exit_time)
            equity.append(current_bal)
        
        # Create chart
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # Color the line green if above start, red if below
        if starting_balance:
            ax.axhline(y=starting_balance, color='gray', linestyle='--', alpha=0.5, label='Starting Balance')
        
        ax.plot(dates, equity, color='#2196F3', linewidth=1.5, label='Equity')
        ax.fill_between(
            dates, equity,
            starting_balance if starting_balance else min(equity),
            alpha=0.1, color='#2196F3'
        )
        
        ax.set_title(f"{title} - Equity Curve", fontsize=14, fontweight='bold')
        ax.set_xlabel("Date", fontsize=10)
        
        if starting_balance:
            ax.set_ylabel("Balance ($)", fontsize=10)
        else:
            ax.set_ylabel("Cumulative PnL ($)", fontsize=10)
        
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper left')
        
        # Format x-axis dates
        if len(dates) > 20:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        # Save to buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        
        return buf
        
    except Exception as e:
        logger.error(f"Failed to generate equity chart: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def _generate_pnl_bar_chart(
    title: str,
    activities: List[Dict[str, Any]]
) -> io.BytesIO:
    """Generate per-trade PnL bar chart."""
    try:
        sorted_acts = sorted(
            [a for a in activities if a.get("exit_time")],
            key=lambda x: x["exit_time"]
        )
        
        if not sorted_acts:
            return None
        
        pnls = [a.get("pnl", 0) or 0 for a in sorted_acts]
        labels = [
            f"{a.get('asset', '?')}\n{(a.get('direction') or '?')[0].upper()}"
            for a in sorted_acts
        ]
        colors = ['#4CAF50' if p >= 0 else '#F44336' for p in pnls]
        
        fig, ax = plt.subplots(figsize=(max(8, len(pnls) * 0.5), 5))
        
        x = range(len(pnls))
        ax.bar(x, pnls, color=colors, alpha=0.8, edgecolor='white', linewidth=0.5)
        ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
        
        ax.set_title(f"{title} - PnL Per Trade", fontsize=14, fontweight='bold')
        ax.set_ylabel("PnL ($)", fontsize=10)
        ax.set_xlabel("Trade #", fontsize=10)
        
        # Only show labels if not too many trades
        if len(pnls) <= 30:
            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, fontsize=7, rotation=45)
        
        ax.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        
        return buf
        
    except Exception as e:
        logger.error(f"Failed to generate PnL bar chart: {e}")
        return None


def _generate_csv(
    activities: List[Dict[str, Any]],
    mode: str = "real"
) -> io.BytesIO:
    """Generate CSV file of trade history."""
    try:
        buf = io.BytesIO()
        text_buf = io.StringIO()
        
        # Define columns
        columns = [
            "Trade Date", "Setup Name", "Asset", "Direction", "Lot Size",
            "Entry Time", "Entry Price", "Exit Time", "Exit Price",
            "PnL (USD)", "PnL (INR)", "Status", "Entry Signal", "Exit Signal"
        ]
        
        if mode == "paper":
            columns.extend(["Leverage", "Margin Used", "Fees", "Liquidation Price"])
        
        writer = csv.writer(text_buf)
        writer.writerow(columns)
        
        for act in activities:
            entry_time = act.get("entry_time", "")
            exit_time = act.get("exit_time", "")
            
            if isinstance(entry_time, datetime):
                entry_time = entry_time.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(exit_time, datetime):
                exit_time = exit_time.strftime("%Y-%m-%d %H:%M:%S")
            
            row = [
                act.get("trade_date", ""),
                act.get("algo_setup_name", ""),
                act.get("asset", ""),
                act.get("direction", ""),
                act.get("lot_size", ""),
                entry_time,
                act.get("entry_price", ""),
                exit_time,
                act.get("exit_price", ""),
                round(act.get("pnl", 0) or 0, 4),
                round(act.get("pnl_inr", 0) or 0, 2),
                "Closed" if act.get("is_closed") else "Open",
                act.get("perusu_entry_signal", ""),
                act.get("sirusu_exit_signal", ""),
            ]
            
            if mode == "paper":
                row.extend([
                    act.get("paper_leverage", ""),
                    round(act.get("paper_margin_used", 0) or 0, 4),
                    round(act.get("paper_fees", 0) or 0, 4),
                    act.get("paper_liquidation_price", ""),
                ])
            
            writer.writerow(row)
        
        # Convert to bytes
        text_buf.seek(0)
        buf.write(text_buf.getvalue().encode('utf-8'))
        buf.seek(0)
        
        return buf
        
    except Exception as e:
        logger.error(f"Failed to generate CSV: {e}")
        return None
