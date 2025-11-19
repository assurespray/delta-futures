import logging
from datetime import datetime
from api.delta_client import DeltaExchangeClient
from api.orders import get_order_status_by_id
from database.mongodb import mongodb  # adjust import as per your project

logger = logging.getLogger(__name__)

async def reconcile_pending_orders(logger_bot=None):
    """
    Poll all DB orders with status 'pending', check Delta Exchange,
    and update to 'filled', 'closed', 'cancelled', or 'not_found' in DB.
    """
    db = mongodb.get_db()
    # Only a reasonable batch per run; you can optimize/batch further if needed
    pending_orders = await db.orders.find({"status": "pending"}).to_list(200)
    if not pending_orders:
        logger.info("No pending orders to reconcile")
        return

    logger.info(f"Reconciling {len(pending_orders)} pending orders")

    for order in pending_orders:
        client = None
        try:
            # Most likely you need to load credentials (per user or globally)
            cred = await get_api_credential_by_id(order["algo_setup_id"], decrypt=True)
            if not cred:
                continue
            client = DeltaExchangeClient(
                api_key=cred['api_key'],
                api_secret=cred['api_secret']
            )

            # Check current order status on the exchange
            status = await get_order_status_by_id(client, order['order_id'])
            if not status:
                await db.orders.update_one(
                    {"order_id": order["order_id"]},
                    {"$set": {"status": "not_found", "updated_at": datetime.utcnow()}}
                )
                logger.warning(f"Order {order['order_id']} not found on exchange; marked not_found in DB")
            else:
                live_status = status.get("state", None)
                await db.orders.update_one(
                    {"order_id": order["order_id"]},
                    {"$set": {"status": live_status, "updated_at": datetime.utcnow()}}
                )
                logger.info(f"Order {order['order_id']} status updated to {live_status}")

        except Exception as e:
            logger.error(f"❌ Error checking order {order.get('order_id')}: {str(e)}")
            if logger_bot:
                await logger_bot.send_error(
                    f"Order reconciliation error: {order.get('order_id')} | {str(e)}"
                )
        finally:
            if client:
                await client.close()
