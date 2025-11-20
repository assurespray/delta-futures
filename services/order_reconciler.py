import logging
from datetime import datetime
from database.crud import get_api_credential_by_id
from database.mongodb import mongodb
from api.delta_client import DeltaExchangeClient
from api.orders import get_order_status_by_id

logger = logging.getLogger(__name__)

async def reconcile_pending_orders(logger_bot=None):
    """
    Poll all DB orders with status 'pending', check Delta Exchange,
    and update to 'filled', 'closed', 'cancelled', or 'not_found' in DB.
    """
    db = mongodb.get_db()
    pending_orders = await db.orders.find({"status": "pending"}).to_list(200)
    
    if not pending_orders:
        logger.info("No pending orders to reconcile")
        return

    logger.info(f"Reconciling {len(pending_orders)} pending orders")
    updated_count = 0
    not_found_count = 0

    for order in pending_orders:
        client = None
        order_id = order.get("order_id")
        algo_setup_id = order.get("algo_setup_id")
        
        try:
            # Load credentials for this order's setup
            cred = await get_api_credential_by_id(algo_setup_id, decrypt=True)
            if not cred:
                logger.warning(f"No credentials for setup {algo_setup_id}, skipping order {order_id}")
                continue
            
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )

            # Check current order status on the exchange
            status = await get_order_status_by_id(client, order_id)
            
            if not status:
                # Order not found on exchange - mark as not_found
                await db.orders.update_one(
                    {"order_id": order_id},
                    {
                        "$set": {
                            "status": "not_found",
                            "updated_at": datetime.utcnow()
                        }
                    }
                )
                not_found_count += 1
                logger.info(f"✅ Order {order_id} not found on exchange; marked not_found in DB")
            else:
                # Sync DB with exchange status
                live_status = status.get("state", "unknown")
                await db.orders.update_one(
                    {"order_id": order_id},
                    {
                        "$set": {
                            "status": live_status,
                            "updated_at": datetime.utcnow()
                        }
                    }
                )
                updated_count += 1
                logger.info(f"✅ Order {order_id} status updated to '{live_status}'")

        except Exception as e:
            logger.error(f"❌ Error checking order {order_id}: {e}")
            if logger_bot:
                await logger_bot.send_error(f"Order reconciliation error: {order_id} | {str(e)}")
        finally:
            if client:
                await client.close()
    
    logger.info(
        f"📊 Reconciliation complete: {updated_count} updated, "
        f"{not_found_count} not found, {len(pending_orders)} total"
    )
