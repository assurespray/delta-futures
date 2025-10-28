"""Telegram bot initialization and handler registration."""
import logging
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)
from config.settings import settings

# Import handlers
from handlers.start import start_command, main_menu_callback, help_callback
from handlers.api_menu import (
    api_menu_callback, api_add_start, api_name_received, api_key_received,
    api_secret_received, api_delete_callback, api_delete_confirm_callback,
    cancel_conversation, API_NAME, API_KEY, API_SECRET
)
from handlers.balance import balance_callback
from handlers.positions import positions_callback
from handlers.orders import (
    orders_callback, order_cancel_callback, order_cancel_all_callback
)
from handlers.indicators import (
    indicators_callback, indicator_select_callback, indicator_timeframe_callback,
    indicator_asset_received, indicator_refresh_callback, cancel_indicator, INDICATOR_ASSET
)
from handlers.algo_setup import (
    algo_setups_callback, algo_add_start, setup_name_received, setup_desc_received,
    setup_api_selected, setup_indicator_selected, setup_direction_selected,
    setup_timeframe_selected, setup_asset_received, setup_lot_size_received,
    setup_protection_selected, setup_confirmed, cancel_algo_setup,
    algo_view_list_callback, algo_view_detail_callback,
    algo_delete_list_callback, algo_delete_confirm_callback,
    SETUP_NAME, SETUP_DESC, SETUP_API, SETUP_INDICATOR, SETUP_DIRECTION,
    SETUP_TIMEFRAME, SETUP_ASSET, SETUP_LOT_SIZE, SETUP_PROTECTION, SETUP_CONFIRM
)
from handlers.algo_activity import algo_activity_callback

logger = logging.getLogger(__name__)


def create_application() -> Application:
    """
    Create and configure Telegram bot application.
    
    Returns:
        Configured Application instance
    """
    # Create application
    application = Application.builder().token(settings.telegram_bot_token).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    
    # API Menu conversation handler
    api_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(api_add_start, pattern="^api_add$")],
        states={
            API_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, api_name_received)],
            API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, api_key_received)],
            API_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, api_secret_received)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False
    )
    application.add_handler(api_conv_handler)
    
    # ✅ FIX: Indicators conversation handler (CORRECTED ENTRY POINT)
    indicator_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(indicator_timeframe_callback, pattern="^indicator_tf_")
        ],
        states={
            INDICATOR_ASSET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, indicator_asset_received),
                CallbackQueryHandler(indicators_callback, pattern="^menu_indicators$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_indicator),
            CallbackQueryHandler(indicators_callback, pattern="^menu_indicators$")
        ],
        per_message=False
    )
    application.add_handler(indicator_conv_handler)
    
    # Algo Setup conversation handler
    algo_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(algo_add_start, pattern="^algo_add_start$")],
        states={
            SETUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name_received)],
            SETUP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_desc_received)],
            SETUP_API: [CallbackQueryHandler(setup_api_selected, pattern="^setup_api_")],
            SETUP_INDICATOR: [CallbackQueryHandler(setup_indicator_selected, pattern="^setup_ind_")],
            SETUP_DIRECTION: [CallbackQueryHandler(setup_direction_selected, pattern="^setup_dir_")],
            SETUP_TIMEFRAME: [CallbackQueryHandler(setup_timeframe_selected, pattern="^setup_tf_")],
            SETUP_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_asset_received)],
            SETUP_LOT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_lot_size_received)],
            SETUP_PROTECTION: [CallbackQueryHandler(setup_protection_selected, pattern="^setup_prot_")],
            SETUP_CONFIRM: [CallbackQueryHandler(setup_confirmed, pattern="^setup_confirm_")]
        },
        fallbacks=[CommandHandler("cancel", cancel_algo_setup)],
        per_message=False
    )
    application.add_handler(algo_conv_handler)
    
    # ============================================================
    # CALLBACK QUERY HANDLERS (Order matters - most specific first)
    # ============================================================
    
    # Main menu
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^menu_help$"))
    
    # API Menu handlers
    application.add_handler(CallbackQueryHandler(api_menu_callback, pattern="^menu_api$"))
    application.add_handler(CallbackQueryHandler(api_delete_callback, pattern="^api_delete$"))
    application.add_handler(CallbackQueryHandler(api_delete_confirm_callback, pattern="^api_delete_confirm_"))
    
    # Balance handler
    application.add_handler(CallbackQueryHandler(balance_callback, pattern="^menu_balance$"))
    
    # Positions handler
    application.add_handler(CallbackQueryHandler(positions_callback, pattern="^menu_positions$"))
    
    # Orders handlers
    application.add_handler(CallbackQueryHandler(orders_callback, pattern="^menu_orders$"))
    application.add_handler(CallbackQueryHandler(order_cancel_callback, pattern="^order_cancel_"))
    application.add_handler(CallbackQueryHandler(order_cancel_all_callback, pattern="^order_cancel_all_"))
    
    # ✅ Indicators handlers (ALL handlers needed)
    application.add_handler(CallbackQueryHandler(indicators_callback, pattern="^menu_indicators$"))
    application.add_handler(CallbackQueryHandler(indicator_select_callback, pattern="^indicator_select_"))
    application.add_handler(CallbackQueryHandler(indicator_refresh_callback, pattern="^indicator_refresh$"))
    # Note: indicator_timeframe_callback is handled by ConversationHandler entry point above
    
    # Algo Setups handlers
    application.add_handler(CallbackQueryHandler(algo_setups_callback, pattern="^menu_algo_setups$"))
    application.add_handler(CallbackQueryHandler(algo_view_list_callback, pattern="^algo_view_list$"))
    application.add_handler(CallbackQueryHandler(algo_view_detail_callback, pattern="^algo_view_"))
    application.add_handler(CallbackQueryHandler(algo_delete_list_callback, pattern="^algo_delete_list$"))
    application.add_handler(CallbackQueryHandler(algo_delete_confirm_callback, pattern="^algo_delete_confirm_"))
    
    # Algo Activity handler
    application.add_handler(CallbackQueryHandler(algo_activity_callback, pattern="^menu_algo_activity$"))
    
    logger.info("✅ Bot handlers registered")
    
    return application
    
