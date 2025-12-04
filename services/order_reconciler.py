import logging
from datetime import datetime
from database.crud import get_api_credential_by_id, get_algo_setup_by_id, get_all_active_algo_setups
from database.mongodb import mongodb
from api.delta_client import DeltaExchangeClient
from api.orders import get_order_status_by_id
from api.orders import is_order_gone
from database.crud import update_algo_setup
from strategy.position_manager import PositionManager

logger = logging.getLogger(__name__)

async def reconcile_pending_orders(logger_bot=None):
    """
    Poll all DB orders with status 'pending', check Delta Exchange,
    and update to 'filled', 'closed', 'cancelled', or 'not_found' in DB.
    Also checks pending entry orders from algo_setups.
    """
    db = mongodb.get_db()
    pending_orders = await db.orders.find({"status": "pending"}).to_list(200)
    
    logger.info(f"Reconciling {len(pending_orders)} pending orders")
    updated_count = 0
    not_found_count = 0

    # Check pending orders in orders collection
    for order in pending_orders:
        client = None
        order_id = order.get("order_id")
        algo_setup_id = order.get("algo_setup_id")
        
        try:
            # Get the algo setup first
            setup = await get_algo_setup_by_id(algo_setup_id)
            if not setup:
                logger.warning(f"Setup {algo_setup_id} not found, skipping order {order_id}")
                continue
            
            # Get credentials using api_id from setup
            api_id = setup.get("api_id")
            if not api_id:
                logger.warning(f"No api_id for setup {algo_setup_id}, skipping order {order_id}")
                continue
            
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred:
                logger.warning(f"No credentials for api_id {api_id}, skipping order {order_id}")
                continue
            
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )

            # Get product_id
            product_id = order.get("product_id") or setup.get("product_id")
            if not product_id:
                logger.warning(f"No product_id for order {order_id}, skipping")
                continue

            # Check current order status on the exchange
            status = await get_order_status_by_id(client, order_id, product_id)
            
            if status == "not_found":
                await db.orders.update_one(
                    {"order_id": order_id},
                    {"$set": {"status": "not_found", "updated_at": datetime.utcnow()}}
                )
                not_found_count += 1
                logger.info(f"✅ Order {order_id} not found; marked not_found")
            else:
                await db.orders.update_one(
                    {"order_id": order_id},
                    {"$set": {"status": status, "updated_at": datetime.utcnow()}}
                )
                updated_count += 1
                logger.info(f"✅ Order {order_id} status updated to '{status}'")

        except Exception as e:
            logger.error(f"❌ Error checking order {order_id}: {e}")
            if logger_bot:
                await logger_bot.send_error(f"Order reconciliation error: {order_id} | {str(e)}")
        finally:
            if client:
                await client.close()
    
    # Check pending entry orders from algo_setups
    position_manager = PositionManager()
    all_setups = await get_all_active_algo_setups()
    
    for setup in all_setups:
        pending_entry_id = setup.get("pending_entry_order_id")
        if not pending_entry_id:
            continue
        
        client = None
        try:
            api_id = setup.get("api_id")
            if not api_id:
                continue
            
            cred = await get_api_credential_by_id(api_id, decrypt=True)
            if not cred:
                continue
            
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )
            
            product_id = setup.get("product_id")

            filled = await position_manager.check_entry_order_filled(
                client, setup, None
            )

            if filled:
                logger.info(f"✅ Pending entry filled for {setup.get('setup_name')}")
            else:
                # If not filled, check if the order is completely gone (cancelled/not found)
                if product_id:
                    gone = await is_order_gone(client, pending_entry_id, product_id)
                    if gone:
                        logger.info(
                            f"🧹 Pending entry {pending_entry_id} for {setup.get('setup_name')} "
                            f"is gone on exchange – clearing from DB"
                        )
                        await update_algo_setup(str(setup["_id"]), {
                            "pending_entry_order_id": None,
                            "pending_entry_direction_signal": None,
                            "pending_entry_side": None,
                        })
            
        except Exception as e:
            logger.error(f"❌ Error checking pending entry for {setup.get('setup_name')}: {e}")
        finally:
            if client:
                await client.close()
    
    logger.info(
        f"📊 Reconciliation complete: {updated_count} updated, "
        f"{not_found_count} not found, {len(pending_orders)} total"
            )
