"""Telegram bot initialization and handler registration."""
import logging
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)
from config.settings import settings

# Import handlers
from handlers.start import start_command, main_menu_callback, help_callback
from handlers.indicator_tracker import tracker_menu_callback, tracker_view_callback
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
from handlers.screener_setup import (
    screener_setups_callback, screener_add_start, screener_name_received, screener_desc_received,
    screener_api_selected, screener_asset_type_selected, screener_timeframe_selected,
    screener_direction_selected, screener_lot_size_received, screener_protection_selected,
    screener_confirmed, cancel_screener_setup,
    screener_view_list_callback, screener_view_detail_callback,
    screener_delete_list_callback, screener_delete_confirm_callback,
    SCREENER_NAME, SCREENER_DESC, SCREENER_API, SCREENER_ASSET_TYPE,
    SCREENER_TIMEFRAME, SCREENER_DIRECTION, SCREENER_LOT_SIZE, SCREENER_PROTECTION, SCREENER_CONFIRM
)
from handlers.algo_activity import algo_activity_callback
from handlers.paper_trading import (
    paper_trading_menu_callback, paper_add_start, paper_name_received,
    paper_desc_received, paper_api_selected, paper_direction_selected,
    paper_timeframe_selected, paper_asset_received, paper_lot_size_received,
    paper_leverage_selected, paper_protection_selected, paper_confirmed,
    cancel_paper_setup, paper_view_list_callback, paper_detail_callback,
    paper_toggle_callback, paper_open_positions_callback,
    paper_delete_list_callback, paper_delete_confirm_callback,
    paper_set_balance_callback, paper_set_balance_amount_received,
    pscr_add_start, pscr_name_received, pscr_desc_received,
    pscr_api_selected, pscr_asset_type_selected, pscr_timeframe_selected,
    pscr_direction_selected, pscr_lot_size_received, pscr_leverage_selected,
    pscr_protection_selected, pscr_confirmed,
    PAPER_NAME, PAPER_DESC, PAPER_API, PAPER_DIRECTION,
    PAPER_TIMEFRAME, PAPER_ASSET, PAPER_LOT_SIZE, PAPER_LEVERAGE,
    PAPER_PROTECTION, PAPER_CONFIRM,
    PSCR_NAME, PSCR_DESC, PSCR_API, PSCR_ASSET_TYPE,
    PSCR_TIMEFRAME, PSCR_DIRECTION, PSCR_LOT_SIZE, PSCR_LEVERAGE,
    PSCR_PROTECTION, PSCR_CONFIRM,
    PAPER_SET_BALANCE_AMOUNT,
)
from handlers.performance import (
    performance_menu_callback, performance_command,
    perf_real_callback, perf_real_chart_callback, perf_real_csv_callback,
    perf_paper_callback, perf_paper_chart_callback,
    perf_paper_pnl_chart_callback, perf_paper_csv_callback
)

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
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False
    )
    application.add_handler(api_conv_handler)
    
    # ✅ FIX: Indicators conversation handler (CORRECTED ENTRY POINT)
    # ✅ Indicators conversation handler
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
            CallbackQueryHandler(indicators_callback, pattern="^menu_indicators$"),
            CallbackQueryHandler(indicator_select_callback, pattern="^indicator_select_"),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
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
        fallbacks=[
            CommandHandler("cancel", cancel_algo_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(algo_conv_handler)
    
    # ============================================================
    # CALLBACK QUERY HANDLERS (Order matters - most specific first)
    # ============================================================
    
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^menu_help$"))
    
    # API Menu handlers
    application.add_handler(CallbackQueryHandler(api_menu_callback, pattern="^menu_api$"))
    application.add_handler(CallbackQueryHandler(api_delete_callback, pattern="^api_delete$"))
    application.add_handler(CallbackQueryHandler(api_delete_confirm_callback, pattern="^api_delete_confirm_"))
    
    # Balance handler
    application.add_handler(CallbackQueryHandler(balance_callback, pattern="^(menu_balance|refresh_balance)$"))
    
    # Positions handler
    application.add_handler(CallbackQueryHandler(positions_callback, pattern="^(menu_positions|refresh_positions)$"))
    
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

    # ===== ADD SCREENER SETUP HANDLERS =====
    
    # Screener Setup conversation handler
    screener_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(screener_add_start, pattern="^screener_add_start$")],
        states={
            SCREENER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, screener_name_received)],
            SCREENER_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, screener_desc_received)],
            SCREENER_API: [CallbackQueryHandler(screener_api_selected, pattern="^screener_api_")],
            SCREENER_ASSET_TYPE: [CallbackQueryHandler(screener_asset_type_selected, pattern="^screener_atype_")],
            SCREENER_TIMEFRAME: [CallbackQueryHandler(screener_timeframe_selected, pattern="^screener_tf_")],
            SCREENER_DIRECTION: [CallbackQueryHandler(screener_direction_selected, pattern="^screener_dir_")],
            SCREENER_LOT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, screener_lot_size_received)],
            SCREENER_PROTECTION: [CallbackQueryHandler(screener_protection_selected, pattern="^screener_prot_")],
            SCREENER_CONFIRM: [CallbackQueryHandler(screener_confirmed, pattern="^screener_confirm_")]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_screener_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(screener_conv_handler)
    
    # Screener Setups other handlers
    application.add_handler(CallbackQueryHandler(screener_setups_callback, pattern="^menu_screener_setups$"))
    application.add_handler(CallbackQueryHandler(screener_view_list_callback, pattern="^screener_view_list$"))
    application.add_handler(CallbackQueryHandler(screener_view_detail_callback, pattern="^screener_view_"))
    application.add_handler(CallbackQueryHandler(screener_delete_list_callback, pattern="^screener_delete_list$"))
    application.add_handler(CallbackQueryHandler(screener_delete_confirm_callback, pattern="^screener_delete_confirm_"))
    
    # Algo Activity handler
    application.add_handler(CallbackQueryHandler(algo_activity_callback, pattern="^menu_algo_activity$"))
    
    # ===== PAPER TRADING HANDLERS =====
    
    # Paper Trading individual setup conversation handler
    paper_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(paper_add_start, pattern="^paper_add_start$")],
        states={
            PAPER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_name_received)],
            PAPER_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_desc_received)],
            PAPER_API: [CallbackQueryHandler(paper_api_selected, pattern="^paper_api_")],
            PAPER_DIRECTION: [CallbackQueryHandler(paper_direction_selected, pattern="^paper_dir_")],
            PAPER_TIMEFRAME: [CallbackQueryHandler(paper_timeframe_selected, pattern="^paper_tf_")],
            PAPER_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_asset_received)],
            PAPER_LOT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_lot_size_received)],
            PAPER_LEVERAGE: [CallbackQueryHandler(paper_leverage_selected, pattern="^paper_lev_")],
            PAPER_PROTECTION: [CallbackQueryHandler(paper_protection_selected, pattern="^paper_prot_")],
            PAPER_CONFIRM: [CallbackQueryHandler(paper_confirmed, pattern="^paper_confirm_")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_paper_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(paper_conv_handler)
    
    # Paper Trading screener setup conversation handler
    pscr_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(pscr_add_start, pattern="^pscr_add_start$")],
        states={
            PSCR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, pscr_name_received)],
            PSCR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, pscr_desc_received)],
            PSCR_API: [CallbackQueryHandler(pscr_api_selected, pattern="^pscr_api_")],
            PSCR_ASSET_TYPE: [CallbackQueryHandler(pscr_asset_type_selected, pattern="^pscr_atype_")],
            PSCR_TIMEFRAME: [CallbackQueryHandler(pscr_timeframe_selected, pattern="^pscr_tf_")],
            PSCR_DIRECTION: [CallbackQueryHandler(pscr_direction_selected, pattern="^pscr_dir_")],
            PSCR_LOT_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pscr_lot_size_received)],
            PSCR_LEVERAGE: [CallbackQueryHandler(pscr_leverage_selected, pattern="^pscr_lev_")],
            PSCR_PROTECTION: [CallbackQueryHandler(pscr_protection_selected, pattern="^pscr_prot_")],
            PSCR_CONFIRM: [CallbackQueryHandler(pscr_confirmed, pattern="^pscr_confirm_")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_paper_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(pscr_conv_handler)
    
    # Paper Trading set virtual balance conversation handler
    paper_balance_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(paper_set_balance_callback, pattern="^paper_set_balance$")],
        states={
            PAPER_SET_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, paper_set_balance_amount_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_paper_setup),
            CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
        ],
        per_message=False,
        allow_reentry=True
    )
    application.add_handler(paper_balance_conv_handler)
    
    # Paper Trading menu and action handlers
    application.add_handler(CallbackQueryHandler(paper_trading_menu_callback, pattern="^menu_paper_trading$"))
    application.add_handler(CallbackQueryHandler(paper_view_list_callback, pattern="^paper_view_list$"))
    application.add_handler(CallbackQueryHandler(paper_detail_callback, pattern="^paper_detail_"))
    application.add_handler(CallbackQueryHandler(paper_toggle_callback, pattern="^paper_toggle_"))
    application.add_handler(CallbackQueryHandler(paper_open_positions_callback, pattern="^paper_open_positions$"))
    application.add_handler(CallbackQueryHandler(paper_delete_list_callback, pattern="^paper_delete_list$"))
    application.add_handler(CallbackQueryHandler(paper_delete_confirm_callback, pattern="^paper_del_confirm_"))
    
    # ===== PERFORMANCE HANDLERS =====
    
    application.add_handler(CommandHandler("performance", performance_command))
    application.add_handler(CallbackQueryHandler(performance_menu_callback, pattern="^menu_performance$"))
    # Register specific patterns before general ones to avoid regex prefix conflicts
    application.add_handler(CallbackQueryHandler(perf_real_chart_callback, pattern="^perf_real_chart$"))
    application.add_handler(CallbackQueryHandler(perf_real_csv_callback, pattern="^perf_real_csv$"))
    application.add_handler(CallbackQueryHandler(perf_real_callback, pattern="^perf_real$"))
    application.add_handler(CallbackQueryHandler(perf_paper_chart_callback, pattern="^perf_paper_chart$"))
    application.add_handler(CallbackQueryHandler(perf_paper_pnl_chart_callback, pattern="^perf_paper_pnl_chart$"))
    application.add_handler(CallbackQueryHandler(perf_paper_csv_callback, pattern="^perf_paper_csv$"))
    application.add_handler(CallbackQueryHandler(perf_paper_callback, pattern="^perf_paper$"))
    
    # Indicator Tracker
    application.add_handler(CallbackQueryHandler(tracker_menu_callback, pattern="^menu_indicator_tracker$"))
    application.add_handler(CallbackQueryHandler(tracker_view_callback, pattern="^tracker_"))
    
    # Main menu - registered LAST so ConversationHandler fallbacks get priority
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    
    logger.info("✅ Bot handlers registered")
    
    return application
    
