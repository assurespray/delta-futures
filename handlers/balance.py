import logging
from decimal import Decimal, ROUND_DOWN
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.crud import get_api_credentials_by_user, get_api_credential_by_id
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)


def _fmt_amount(value, max_decimals=6):
    """Format a numeric amount cleanly — strip trailing zeros, cap decimal places."""
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    if d == 0:
        return "0"
    # Round down to max_decimals
    d = d.quantize(Decimal(10) ** -max_decimals, rounding=ROUND_DOWN)
    # Normalize to strip trailing zeros, but keep at least 2 decimals for fiat
    return f"{d:f}".rstrip('0').rstrip('.')


def _fmt_inr(value):
    """Format INR value to 2 decimal places."""
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{d:.2f}"


async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching balances...")

    user_id = str(query.from_user.id)
    credentials = await get_api_credentials_by_user(user_id)

    if not credentials:
        keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "ℹ️ No API credentials stored.\n\n"
            "Please add API credentials first from the API Menu.",
            reply_markup=reply_markup
        )
        return

    message = "💵 **Account Balances**\n\n"

    for cred in credentials:
        api_name = cred['api_name']
        cred_id = str(cred['_id'])
        try:
            full_cred = await get_api_credential_by_id(cred_id, decrypt=True)
            if not full_cred:
                message += f"❌ **{api_name}**: Failed to load credentials\n\n"
                continue

            client = DeltaExchangeClient(
                api_key=full_cred['api_key'],
                api_secret=full_cred['api_secret']
            )

            balances_resp = await client.get("/v2/wallet/balances")
            await client.close()
            logger.info(f"Fetched wallet balances for {api_name}: {balances_resp}")

            balances = balances_resp['result'] if balances_resp and 'result' in balances_resp else []
            if not balances:
                message += f"❌ **{api_name}**: No wallet balances found\n\n"
                continue

            # Filter out zero-balance assets
            active = []
            for asset in balances:
                bal = float(asset.get('balance', 0))
                if bal > 0:
                    active.append(asset)

            if not active:
                message += f"✅ **{api_name}**\n└ No active balances\n\n"
                continue

            # Sum total INR value across all assets
            total_inr = Decimal('0')
            for asset in active:
                inr_val = asset.get('balance_inr')
                if inr_val is not None:
                    try:
                        total_inr += Decimal(str(inr_val))
                    except Exception:
                        pass

            message += f"✅ **{api_name}**"
            if total_inr > 0:
                message += f"  (Total: ₹{_fmt_inr(total_inr)})"
            message += "\n"

            for i, asset in enumerate(active):
                sym = asset.get('asset_symbol', asset.get('symbol', ''))
                bal = asset.get('balance', 0)
                avail = asset.get('available_balance', bal)
                locked = float(asset.get('locked_balance', 0))
                inr_total = asset.get('balance_inr')

                is_last = (i == len(active) - 1)
                prefix = "└" if is_last else "├"

                # Format amounts
                bal_str = _fmt_amount(bal)
                avail_str = _fmt_amount(avail)
                inr_str = f" (₹{_fmt_inr(inr_total)})" if inr_total is not None else ""

                line = f"{prefix} **{sym}**: {bal_str}{inr_str}"
                # Only show available/locked if they differ from total
                if locked > 0:
                    line += f"\n{'│' if not is_last else ' '}   Avail: {avail_str} · Locked: {_fmt_amount(locked)}"
                message += line + "\n"

            message += "\n"

        except Exception as e:
            logger.error(f"❌ Error fetching balance for {api_name}: {e}")
            message += f"❌ **{api_name}**: Error - {str(e)[:50]}\n\n"

    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh_balance"),
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
