"""Logger bot for sending notifications and logs."""
import logging
from typing import Optional
from telegram import Bot
from telegram.error import TelegramError
from config.settings import settings

logger = logging.getLogger(__name__)


class LoggerBot:
    """Secondary Telegram bot for logging and notifications."""
    
    def __init__(self):
        """Initialize logger bot and main bot for user notifications."""
        try:
            self.bot = Bot(token=settings.telegram_logger_bot_token)
            self.chat_id = settings.telegram_logger_chat_id
            self.flip_chat_id = settings.telegram_flip_chat_id
            self.trade_chat_id = settings.telegram_trade_chat_id
            self.paper_chat_id = settings.telegram_paper_chat_id
            self.enabled = True
            logger.info("✅ Logger bot initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize logger bot: {e}")
            self.enabled = False
        
        # Initialize main bot for sending trade details to user chat
        try:
            self.main_bot = Bot(token=settings.telegram_bot_token)
            self.main_bot_enabled = True
            logger.info("✅ Main bot reference initialized for trade notifications")
        except Exception as e:
            logger.error(f"❌ Failed to initialize main bot reference: {e}")
            self.main_bot_enabled = False
    
    async def send_message(self, message: str, parse_mode: str = "Markdown", chat_id: Optional[str] = None):
        """
        Send message via logger bot.
        
        Args:
            message: Message to send
            parse_mode: Parse mode (Markdown or HTML)
            chat_id: Optional target chat ID (defaults to default logger chat_id)
        """
        if not self.enabled:
            return
        
        target_chat_id = chat_id if chat_id else self.chat_id
        try:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                parse_mode=parse_mode
            )
        except TelegramError as e:
            logger.error(f"❌ Failed to send logger bot message: {e}")
        except Exception as e:
            logger.error(f"❌ Exception sending logger bot message: {e}")
    
    async def send_to_user(self, user_id: str, message: str, parse_mode: str = "Markdown"):
        """
        Send message to a specific user via the MAIN Telegram bot.
        Used for trade entry/exit notifications directly to the user's chat.
        
        Args:
            user_id: Telegram user/chat ID
            message: Message to send
            parse_mode: Parse mode (Markdown or HTML)
        """
        if not self.main_bot_enabled:
            logger.warning("⚠️ Main bot not available for user notification")
            return
        
        try:
            await self.main_bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=parse_mode
            )
        except TelegramError as e:
            logger.error(f"❌ Failed to send main bot message to {user_id}: {e}")
        except Exception as e:
            logger.error(f"❌ Exception sending main bot message to {user_id}: {e}")

    async def send_trade_alert(self, message: str, parse_mode: str = "Markdown"):
        """
        Send REAL trade alert to the configured trade chat ID using the Logger bot.
        Falls back to default logger chat if no trade chat ID is set.
        """
        await self.send_message(message, parse_mode=parse_mode, chat_id=self.trade_chat_id)

    async def send_paper_alert(self, message: str, parse_mode: str = "Markdown"):
        """
        Send PAPER trade alert to the configured paper chat ID using the Logger bot.
        Falls back to default logger chat if no paper chat ID is set.
        """
        await self.send_message(message, parse_mode=parse_mode, chat_id=self.paper_chat_id)

    async def send_info(self, message: str):
        """Send info level message."""
        formatted = f"ℹ️ **INFO**\n{message}\n\n_Time: {self._get_timestamp()}_"
        await self.send_message(formatted)
    
    async def send_error(self, message: str):
        """Send error level message."""
        formatted = f"❌ **ERROR**\n{message}\n\n_Time: {self._get_timestamp()}_"
        await self.send_message(formatted)
    
    async def send_warning(self, message: str):
        """Send warning level message."""
        formatted = f"⚠️ **WARNING**\n{message}\n\n_Time: {self._get_timestamp()}_"
        await self.send_message(formatted)
    
    async def send_trade_entry(self, setup_name: str, asset: str, direction: str,
                              entry_price: float, lot_size: int, signal_text: str,
                              stop_loss: Optional[float] = None,
                              api_name: Optional[str] = None):
        """
        Send trade entry notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            direction: Trade direction (long/short)
            entry_price: Entry price
            lot_size: Lot size
            signal_text: Entry signal text (e.g., "Uptrend", "Downtrend")
            stop_loss: Stop-loss price value
            api_name: API account name (e.g., "Main Account")
        """
        emoji = "🟢" if direction == "long" else "🔴"
        
        # Guard against None values in formatting
        price_str = f"${float(entry_price):.2f}" if entry_price is not None else "N/A"
        
        message = f"{emoji} **TRADE ENTRY**\n\n"
        if api_name:
            message += f"**API:** {api_name}\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**Direction:** {direction.upper()}\n"
        message += f"**Entry Price:** {price_str}\n"
        message += f"**Lot Size:** {lot_size} contracts\n"
        message += f"**Signal:** {signal_text}\n"
        
        if stop_loss is not None:
            message += f"**Stop-Loss:** ${float(stop_loss):.2f}\n"
        
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message, chat_id=self.trade_chat_id)
    
    async def send_trade_exit(self, setup_name: str, asset: str, direction: str,
                             exit_reason: str):
        """
        Send trade exit notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            direction: Trade direction
            exit_reason: Exit reason text
        """
        message = f"🚪 **TRADE EXIT**\n\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**Direction:** {direction.upper()}\n"
        message += f"**Exit Reason:** {exit_reason}\n"
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message, chat_id=self.trade_chat_id)
    
    async def send_pnl_summary(self, setup_name: str, asset: str, pnl_usd: float, pnl_inr: float):
        """
        Send PnL summary notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            pnl_usd: PnL in USD
            pnl_inr: PnL in INR
        """
        emoji = "💰" if (pnl_usd is not None and pnl_usd >= 0) else "📉"
        pnl_usd_str = f"${float(pnl_usd):.2f}" if pnl_usd is not None else "N/A"
        pnl_inr_str = f"₹{float(pnl_inr):.2f}" if pnl_inr is not None else "N/A"
        
        message = f"{emoji} **PnL UPDATE**\n\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**PnL:** {pnl_usd_str} ({pnl_inr_str})\n"
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message, chat_id=self.trade_chat_id)
    
    def _get_timestamp(self) -> str:
        """Get formatted timestamp in IST."""
        from datetime import datetime, timedelta
        ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        return ist.strftime("%Y-%m-%d %H:%M:%S IST")

    async def send_order_cancelled(self, setup_name: str, old_signal: str, new_signal: str):
        """
        Send order cancellation notification.

        Args:
            setup_name: Algo setup name
            old_signal: Original signal (e.g., "Uptrend" or "Downtrend")
            new_signal: New signal (e.g., "Uptrend" or "Downtrend")
        """
        message = (
            f"⚠️ **ORDER CANCELLED**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Reason:** Primary signal reversed\n\n"
            f"**Old signal:** {old_signal}\n"
            f"**New signal:** {new_signal}\n\n"
            f"_No bad trade - signal protection active!_\n"
            f"_Time: {self._get_timestamp()}_"
        )
        await self.send_message(message, chat_id=self.trade_chat_id)

    async def send_indicator_flip(self, setup_name: str, asset: str, timeframe: str,
                                  indicator_name: str, old_signal_text: str, new_signal_text: str,
                                  primary_name: str = "Primary", primary_signal: int = 0,
                                  primary_value: float = None,
                                  secondary_name: str = "Secondary", secondary_signal: int = 0,
                                  secondary_value: float = None,
                                  current_price: float = None):
        """
        Send indicator flip notification to Telegram.
        Works for ANY strategy - uses the dynamic indicator names from the strategy's cache mapping.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset symbol
            timeframe: Candle timeframe
            indicator_name: Display name of the indicator that flipped (e.g., "Perusu", "Single ST")
            old_signal_text: Previous signal text (e.g., "Uptrend")
            new_signal_text: Current signal text (e.g., "Downtrend")
            primary_name: Display name of primary indicator
            primary_signal: Current primary signal (1 or -1)
            primary_value: Current primary indicator value
            secondary_name: Display name of secondary indicator
            secondary_signal: Current secondary signal (1 or -1)
            secondary_value: Current secondary indicator value
            current_price: Current asset price
        """
        flip_emoji = "📈" if new_signal_text == "Uptrend" else "📉"
        primary_text = "UPTREND" if primary_signal == 1 else "DOWNTREND"
        secondary_text = "UPTREND" if secondary_signal == 1 else "DOWNTREND"
        
        message = (
            f"🔄 **INDICATOR FLIP**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} ({timeframe})\n"
            f"**Indicator:** {indicator_name}\n"
            f"**Flip:** {old_signal_text} {flip_emoji} {new_signal_text}\n\n"
            f"**Current State:**\n"
        )
        
        if current_price is not None:
            message += f"├ Price: ${current_price:.2f}\n"
        
        # Show primary indicator
        p_emoji = "📈" if primary_signal == 1 else "📉"
        message += f"├ {primary_name}: {p_emoji} {primary_text}"
        if primary_value is not None:
            message += f" (${primary_value:.5f})"
        message += "\n"
        
        # Only show secondary if it's different from primary
        if primary_name != secondary_name:
            s_emoji = "📈" if secondary_signal == 1 else "📉"
            message += f"├ {secondary_name}: {s_emoji} {secondary_text}"
            if secondary_value is not None:
                message += f" (${secondary_value:.5f})"
            message += "\n"
        
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message, chat_id=self.flip_chat_id)

    async def send_flip_log(self, setup_name: str, asset: str, timeframe: str,
                           indicator_name: str, old_signal_text: str, new_signal_text: str,
                           primary_signal: int, secondary_signal: int,
                           current_position: str, action: str,
                           primary_value: float = None, secondary_value: float = None,
                           current_price: float = None,
                           primary_name: str = "Primary", secondary_name: str = "Secondary"):
        """
        Send detailed indicator flip notification to log bot.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset symbol
            timeframe: Candle timeframe
            indicator_name: Display name of the indicator that flipped
            old_signal_text: Previous signal text (e.g., "Uptrend")
            new_signal_text: Current signal text (e.g., "Downtrend")
            primary_signal: Current primary signal (1 or -1)
            secondary_signal: Current secondary signal (1 or -1)
            current_position: Current position ("long", "short", or None)
            action: Description of what the engine will do
            primary_value: Current primary indicator value
            secondary_value: Current secondary indicator value
            current_price: Current asset price
            primary_name: Display name of primary indicator
            secondary_name: Display name of secondary indicator
        """
        primary_text = "UPTREND (1)" if primary_signal == 1 else "DOWNTREND (-1)"
        secondary_text = "UPTREND (1)" if secondary_signal == 1 else "DOWNTREND (-1)"
        
        pos_text = current_position.upper() if current_position else "FLAT (No Position)"
        
        message = (
            f"🔄 **INDICATOR FLIP DETECTED**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n\n"
            f"**Flip:** {indicator_name}: {old_signal_text} -> {new_signal_text}\n\n"
            f"**Current State:**\n"
            f"├ {primary_name}: {primary_text}\n"
            f"├ {secondary_name}: {secondary_text}\n"
            f"├ Position: {pos_text}\n"
        )
        
        if current_price is not None:
            message += f"├ Price: ${current_price}\n"
        if primary_value is not None:
            message += f"├ {primary_name} Value: ${primary_value:.5f}\n"
        if secondary_value is not None:
            message += f"├ {secondary_name} Value: ${secondary_value:.5f}\n"
        
        message += (
            f"└ **Action:** {action}\n\n"
            f"_Time: {self._get_timestamp()}_"
        )
        
        await self.send_message(message, chat_id=self.flip_chat_id)

    async def send_no_signal_log(self, setup_name: str, asset: str, timeframe: str,
                                primary_signal: int, secondary_signal: int,
                                current_position: str, reason: str,
                                primary_name: str = "Primary", secondary_name: str = "Secondary"):
        """
        Send a log when indicators are calculated but no actionable signal is generated.
        """
        primary_text = "UPTREND" if primary_signal == 1 else "DOWNTREND"
        secondary_text = "UPTREND" if secondary_signal == 1 else "DOWNTREND"
        pos_text = current_position.upper() if current_position else "FLAT"
        
        message = (
            f"ℹ️ **NO SIGNAL**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n\n"
            f"**State:**\n"
            f"├ {primary_name}: {primary_text}\n"
            f"├ {secondary_name}: {secondary_text}\n"
            f"├ Position: {pos_text}\n"
            f"└ **Reason:** {reason}\n\n"
            f"_Time: {self._get_timestamp()}_"
        )
        
        await self.send_message(message, chat_id=self.flip_chat_id)

    async def send_trade_entry_detail(self, setup_name: str, asset: str, timeframe: str,
                                     direction: str, entry_price: float, lot_size: int,
                                     primary_signal_text: str, secondary_signal_text: str,
                                     primary_value: float, secondary_value: float,
                                     stop_loss_price: float = None,
                                     entry_type: str = "market",
                                     entry_order_id=None, sl_order_id=None,
                                     primary_name: str = "Primary", secondary_name: str = "Secondary",
                                     api_name: Optional[str] = None):
        """
        Send detailed trade entry notification (for main bot user chat).
        """
        emoji = "🟢" if direction == "long" else "🔴"
        
        # Guard against None values in formatting
        ep_str = f"${float(entry_price):.5f}" if entry_price is not None else "N/A"
        pv_str = f"${float(primary_value):.5f}" if primary_value is not None else "N/A"
        sv_str = f"${float(secondary_value):.5f}" if secondary_value is not None else "N/A"
        
        message = f"{emoji} **TRADE ENTRY**\n\n"
        if api_name:
            message += f"**API:** {api_name}\n"
        message += (
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n"
            f"**Direction:** {direction.upper()}\n"
            f"**Entry Type:** {entry_type.upper()}\n"
            f"**Entry Price:** {ep_str}\n"
            f"**Lot Size:** {lot_size} contracts\n\n"
            f"**Indicators at Entry:**\n"
            f"├ {primary_name}: {primary_signal_text} ({pv_str})\n"
            f"├ {secondary_name}: {secondary_signal_text} ({sv_str})\n"
        )
        
        if stop_loss_price is not None:
            message += f"├ Stop Loss: ${float(stop_loss_price):.5f}\n"
        
        if entry_order_id:
            message += f"├ Entry Order ID: {entry_order_id}\n"
        if sl_order_id:
            message += f"├ SL Order ID: {sl_order_id}\n"
        
        message += f"└ Time: {self._get_timestamp()}"
        
        await self.send_message(message, chat_id=self.trade_chat_id)

    async def send_trade_exit_detail(self, setup_name: str, asset: str, timeframe: str,
                                    direction: str, entry_price: float, exit_price: float,
                                    lot_size: int, pnl_usd: float = None, pnl_inr: float = None,
                                    exit_signal_text: str = "",
                                    exit_reason: str = "Indicator flip",
                                    entry_order_id=None, sl_order_id=None, exit_order_id=None,
                                    api_name: Optional[str] = None):
        """
        Send detailed trade exit notification (for main bot user chat).
        """
        pnl_emoji = "💰" if (pnl_usd is not None and pnl_usd >= 0) else "📉"
        
        # Guard against None values in formatting
        ep_str = f"${float(entry_price):.5f}" if entry_price is not None else "N/A"
        xp_str = f"${float(exit_price):.5f}" if exit_price is not None else "N/A"
        
        message = f"🚪 **TRADE EXIT**\n\n"
        if api_name:
            message += f"**API:** {api_name}\n"
        message += (
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n"
            f"**Direction:** {direction.upper()}\n"
            f"**Exit Reason:** {exit_reason}\n\n"
            f"**Trade Details:**\n"
            f"├ Entry Price: {ep_str}\n"
            f"├ Exit Price: {xp_str}\n"
            f"├ Lot Size: {lot_size} contracts\n"
            f"├ Exit Signal: {exit_signal_text}\n"
        )
        
        if pnl_usd is not None:
            message += f"├ {pnl_emoji} PnL: ${pnl_usd:.4f}"
            if pnl_inr is not None:
                message += f" (₹{pnl_inr:.2f})"
            message += "\n"
        
        if entry_order_id:
            message += f"├ Entry Order ID: {entry_order_id}\n"
        if sl_order_id:
            message += f"├ SL Order ID: {sl_order_id}\n"
        if exit_order_id:
            message += f"├ Exit Order ID: {exit_order_id}\n"
        
        message += f"└ Time: {self._get_timestamp()}"
        
        await self.send_message(message, chat_id=self.trade_chat_id)

# Global logger bot instance
logger_bot = LoggerBot()
