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
    
    async def send_message(self, message: str, parse_mode: str = "Markdown"):
        """
        Send message via logger bot.
        
        Args:
            message: Message to send
            parse_mode: Parse mode (Markdown or HTML)
        """
        if not self.enabled:
            return
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
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
                              entry_price: float, lot_size: int, perusu_signal: str,
                              sirusu_sl: Optional[float] = None):
        """
        Send trade entry notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            direction: Trade direction (long/short)
            entry_price: Entry price
            lot_size: Lot size
            perusu_signal: Perusu signal text
            sirusu_sl: Sirusu stop-loss value
        """
        emoji = "🟢" if direction == "long" else "🔴"
        
        message = f"{emoji} **TRADE ENTRY**\n\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**Direction:** {direction.upper()}\n"
        message += f"**Entry Price:** ${entry_price:.2f}\n"
        message += f"**Lot Size:** {lot_size} contracts\n"
        message += f"**Perusu Signal:** {perusu_signal}\n"
        
        if sirusu_sl:
            message += f"**Stop-Loss:** ${sirusu_sl:.2f}\n"
        
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message)
    
    async def send_trade_exit(self, setup_name: str, asset: str, direction: str,
                             sirusu_signal: str):
        """
        Send trade exit notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            direction: Trade direction
            sirusu_signal: Sirusu exit signal text
        """
        message = f"🚪 **TRADE EXIT**\n\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**Direction:** {direction.upper()}\n"
        message += f"**Sirusu Signal:** {sirusu_signal}\n"
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message)
    
    async def send_pnl_summary(self, setup_name: str, asset: str, pnl_usd: float, pnl_inr: float):
        """
        Send PnL summary notification.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset
            pnl_usd: PnL in USD
            pnl_inr: PnL in INR
        """
        emoji = "💰" if pnl_usd >= 0 else "📉"
        
        message = f"{emoji} **PnL UPDATE**\n\n"
        message += f"**Setup:** {setup_name}\n"
        message += f"**Asset:** {asset}\n"
        message += f"**PnL:** ${pnl_usd:.2f} (₹{pnl_inr:.2f})\n"
        message += f"\n_Time: {self._get_timestamp()}_"
        
        await self.send_message(message)
    
    def _get_timestamp(self) -> str:
        """Get formatted timestamp."""
        from datetime import datetime
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

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
            f"**Reason:** Perusu signal reversed\n\n"
            f"**Old signal:** {old_signal}\n"
            f"**New signal:** {new_signal}\n\n"
            f"_No bad trade - signal protection active!_\n"
            f"_Time: {self._get_timestamp()}_"
        )
        await self.send_message(message)

    async def send_flip_log(self, setup_name: str, asset: str, timeframe: str,
                           indicator_name: str, old_signal_text: str, new_signal_text: str,
                           perusu_signal: int, sirusu_signal: int,
                           current_position: str, action: str,
                           perusu_value: float = None, sirusu_value: float = None,
                           current_price: float = None):
        """
        Send detailed indicator flip notification to log bot.
        
        Args:
            setup_name: Algo setup name
            asset: Trading asset symbol
            timeframe: Candle timeframe
            indicator_name: "perusu" or "sirusu"
            old_signal_text: Previous signal text (e.g., "Uptrend")
            new_signal_text: Current signal text (e.g., "Downtrend")
            perusu_signal: Current perusu signal (1 or -1)
            sirusu_signal: Current sirusu signal (1 or -1)
            current_position: Current position ("long", "short", or None)
            action: Description of what the engine will do
            perusu_value: Current perusu supertrend value
            sirusu_value: Current sirusu supertrend value
            current_price: Current asset price
        """
        ind_emoji = "🟢" if indicator_name == "perusu" else "🔴"
        ind_label = "Perusu" if indicator_name == "perusu" else "Sirusu"
        
        perusu_text = "UPTREND (1)" if perusu_signal == 1 else "DOWNTREND (-1)"
        sirusu_text = "UPTREND (1)" if sirusu_signal == 1 else "DOWNTREND (-1)"
        
        pos_text = current_position.upper() if current_position else "FLAT (No Position)"
        
        message = (
            f"🔄 **INDICATOR FLIP DETECTED**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n\n"
            f"**Flip:** {ind_emoji} {ind_label}: {old_signal_text} → {new_signal_text}\n\n"
            f"**Current State:**\n"
            f"├ 🟢 Perusu: {perusu_text}\n"
            f"├ 🔴 Sirusu: {sirusu_text}\n"
            f"├ 📍 Position: {pos_text}\n"
        )
        
        if current_price is not None:
            message += f"├ 💰 Price: ${current_price}\n"
        if perusu_value is not None:
            message += f"├ 🟢 Perusu Value: ${perusu_value:.5f}\n"
        if sirusu_value is not None:
            message += f"├ 🔴 Sirusu Value: ${sirusu_value:.5f}\n"
        
        message += (
            f"└ ⚡ **Action:** {action}\n\n"
            f"_Time: {self._get_timestamp()}_"
        )
        
        await self.send_message(message)

    async def send_no_signal_log(self, setup_name: str, asset: str, timeframe: str,
                                perusu_signal: int, sirusu_signal: int,
                                current_position: str, reason: str):
        """
        Send a log when indicators are calculated but no actionable signal is generated.
        Useful for tracking post-exit sirusu flips that are intentionally ignored.
        """
        perusu_text = "UPTREND" if perusu_signal == 1 else "DOWNTREND"
        sirusu_text = "UPTREND" if sirusu_signal == 1 else "DOWNTREND"
        pos_text = current_position.upper() if current_position else "FLAT"
        
        message = (
            f"ℹ️ **NO SIGNAL**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n\n"
            f"**State:**\n"
            f"├ 🟢 Perusu: {perusu_text}\n"
            f"├ 🔴 Sirusu: {sirusu_text}\n"
            f"├ 📍 Position: {pos_text}\n"
            f"└ 💤 **Reason:** {reason}\n\n"
            f"_Time: {self._get_timestamp()}_"
        )
        
        await self.send_message(message)

    async def send_trade_entry_detail(self, setup_name: str, asset: str, timeframe: str,
                                     direction: str, entry_price: float, lot_size: int,
                                     perusu_signal_text: str, sirusu_signal_text: str,
                                     perusu_value: float, sirusu_value: float,
                                     stop_loss_price: float = None,
                                     entry_type: str = "market"):
        """
        Send detailed trade entry notification (for main bot user chat).
        """
        emoji = "🟢" if direction == "long" else "🔴"
        
        message = (
            f"{emoji} **TRADE ENTRY**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n"
            f"**Direction:** {direction.upper()}\n"
            f"**Entry Type:** {entry_type.upper()}\n"
            f"**Entry Price:** ${entry_price:.5f}\n"
            f"**Lot Size:** {lot_size} contracts\n\n"
            f"**Indicators at Entry:**\n"
            f"├ 🟢 Perusu: {perusu_signal_text} (${perusu_value:.5f})\n"
            f"├ 🔴 Sirusu: {sirusu_signal_text} (${sirusu_value:.5f})\n"
        )
        
        if stop_loss_price:
            message += f"├ 🛡️ Stop Loss: ${stop_loss_price:.5f}\n"
        
        message += f"└ ⏰ Time: {self._get_timestamp()}"
        
        await self.send_message(message)

    async def send_trade_exit_detail(self, setup_name: str, asset: str, timeframe: str,
                                    direction: str, entry_price: float, exit_price: float,
                                    lot_size: int, pnl_usd: float = None, pnl_inr: float = None,
                                    sirusu_signal_text: str = "",
                                    exit_reason: str = "Sirusu flip"):
        """
        Send detailed trade exit notification (for main bot user chat).
        """
        pnl_emoji = "💰" if (pnl_usd and pnl_usd >= 0) else "📉"
        
        message = (
            f"🚪 **TRADE EXIT**\n\n"
            f"**Setup:** {setup_name}\n"
            f"**Asset:** {asset} @ {timeframe}\n"
            f"**Direction:** {direction.upper()}\n"
            f"**Exit Reason:** {exit_reason}\n\n"
            f"**Trade Details:**\n"
            f"├ Entry Price: ${entry_price:.5f}\n"
            f"├ Exit Price: ${exit_price:.5f}\n"
            f"├ Lot Size: {lot_size} contracts\n"
            f"├ 🔴 Sirusu Signal: {sirusu_signal_text}\n"
        )
        
        if pnl_usd is not None:
            message += f"├ {pnl_emoji} PnL: ${pnl_usd:.4f}"
            if pnl_inr is not None:
                message += f" (₹{pnl_inr:.2f})"
            message += "\n"
        
        message += f"└ ⏰ Time: {self._get_timestamp()}"
        
        await self.send_message(message)

# Global logger bot instance
logger_bot = LoggerBot()
