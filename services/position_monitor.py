import logging
from datetime import datetime
from database.crud import get_api_credential_by_id, get_algo_setup_by_id, get_all_active_algo_setups, update_algo_setup, update_algo_activity, get_open_activity_by_setup
from database.mongodb import mongodb
from api.delta_client import DeltaExchangeClient

logger = logging.getLogger(__name__)

async def reconcile_positions(logger_bot=None):
    """
    Poll all open positions in DB, check Delta Exchange,
    and close positions in DB if found closed on exchange.
    Sync activity and setup as needed.
    """
    db = mongodb.get_db()
    open_positions = await db.positions.find({"status": "open"}).to_list(200)

    logger.info(f"Reconciling {len(open_positions)} open positions")
    closed_count = 0

    for pos in open_positions:
        setup_id = pos.get("algo_setup_id")
        symbol = pos.get("asset")
        try:
            setup = await get_algo_setup_by_id(setup_id)
            if not setup:
                logger.warning(f"No setup for id {setup_id}, skipping position {symbol}")
                continue

            api_id = setup.get("api_id")
            if not api_id:
                logger.warning(f"No api_id for setup {setup_id}, skipping position {symbol}")
                continue
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred:
                logger.warning(f"No credentials for api_id {api_id}, skipping {symbol}")
                continue

            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )

            # Fetch live position for symbol
            exchange_pos = await client.get_position_by_symbol(symbol)
            actual_size = exchange_pos.get("size", 0) if exchange_pos else 0

            if actual_size == 0:
                # Position is closed on exchange, but open in DB
                await db.positions.update_one(
                    {"_id": pos["_id"]},
                    {"$set": {"status": "closed", "closed_at": datetime.utcnow()}}
                )
                await update_algo_setup(setup_id, {"current_position": None})

                # Mark activity as closed with exit time (optional, if you track activity)
                activity = await get_open_activity_by_setup(setup_id)
                if activity:
                    await update_algo_activity(activity["_id"], {
                        "exit_time": datetime.utcnow(),
                        "is_closed": True
                    })

                closed_count += 1
                logger.info(f"✅ Position for {symbol} closed (manual/external); DB updated.")

            await client.close()
        except Exception as e:
            logger.error(f"Error in position reconciliation for {symbol}: {e}")

    logger.info(f"📊 Position reconciliation complete: {closed_count} closed, {len(open_positions)} total")
