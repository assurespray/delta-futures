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
        """Initialize logger bot."""
        try:
            self.bot = Bot(token=settings.telegram_logger_bot_token)
            self.chat_id = settings.telegram_logger_chat_id
            self.enabled = True
            logger.info("✅ Logger bot initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize logger bot: {e}")
            self.enabled = False
    
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


# Global logger bot instance
logger_bot = LoggerBot()
