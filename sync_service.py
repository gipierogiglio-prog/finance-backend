"""
Sync service — orchestrates data sync from Pluggy to local database.
Supports manual sync and can be scheduled via background task.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from database import (
    get_db,
    init_db,
    upsert_account,
    upsert_transaction,
    upsert_investment,
    log_sync,
    get_last_sync,
)
from pluggy_client import PluggyClient, PluggyItemNotReadyError

logger = logging.getLogger(__name__)

# Default sync period (hours)
DEFAULT_SYNC_INTERVAL_HOURS = 6

# Default lookback for transactions (days)
DEFAULT_LOOKBACK_DAYS = 90


class SyncResult:
    """Result of a sync operation."""

    def __init__(
        self,
        success: bool = False,
        items_count: int = 0,
        accounts_count: int = 0,
        transactions_count: int = 0,
        investments_count: int = 0,
        error_message: Optional[str] = None,
        oauth_url: Optional[str] = None,
    ):
        self.success = success
        self.items_count = items_count
        self.accounts_count = accounts_count
        self.transactions_count = transactions_count
        self.investments_count = investments_count
        self.error_message = error_message
        self.oauth_url = oauth_url

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "items_count": self.items_count,
            "accounts_count": self.accounts_count,
            "transactions_count": self.transactions_count,
            "investments_count": self.investments_count,
            "error_message": self.error_message,
            "oauth_url": self.oauth_url,
        }


def get_pluggy_client() -> PluggyClient:
    """Create a PluggyClient with credentials from environment."""
    client_id = os.getenv("PLUGGY_CLIENT_ID", "")
    client_secret = os.getenv("PLUGGY_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise ValueError(
            "PLUGGY_CLIENT_ID and PLUGGY_CLIENT_SECRET must be set in environment"
        )

    return PluggyClient(client_id=client_id, client_secret=client_secret)


def run_sync(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> SyncResult:
    """
    Run a full sync: fetch accounts, transactions, investments from Pluggy
    and save to local SQLite database.

    This is a synchronous function. Run in a thread if called from async context.
    """
    logger.info("Starting sync...")
    result = SyncResult()

    try:
        # Ensure database tables exist
        init_db()

        with get_pluggy_client() as client:
            # ── Get all Items ────────────────────────────────
            try:
                items = client.ensure_all_items()
                if not items:
                    raise PluggyItemNotReadyError("No items available")
                result.items_count = len(items)
                logger.info(f"Using {len(items)} Item(s)")
            except PluggyItemNotReadyError as e:
                logger.warning(f"Items not ready: {e}")
                msg = str(e)
                oauth_url = None
                for part in msg.split():
                    if part.startswith("http"):
                        oauth_url = part.strip(".")
                        break
                result.success = False
                result.error_message = msg
                result.oauth_url = oauth_url
                with get_db() as conn:
                    log_sync(conn, status="WAITING_USER_INPUT", error_message=msg)
                return result

            total_transactions = 0
            total_accounts = 0
            total_investments = 0

            date_to = datetime.now().strftime("%Y-%m-%d")
            date_from = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

            with get_db() as conn:
                for item in items:
                    item_id = item["id"]
                    logger.info(f"Processing Item: {item_id}")

                    # ── Fetch Accounts ─────────────────────────
                    try:
                        accounts = client.list_accounts(item_id)
                        logger.info(f"  Found {len(accounts)} accounts")
                        for account in accounts:
                            upsert_account(conn, account)
                        total_accounts += len(accounts)
                    except Exception as e:
                        logger.warning(f"  Error fetching accounts: {e}")
                        accounts = []

                    # ── Fetch Transactions ─────────────────────
                    for account in accounts:
                        acc_id = account["id"]
                        try:
                            txs = client.get_transactions(acc_id, date_from, date_to)
                            for tx in txs:
                                upsert_transaction(conn, tx, acc_id)
                            total_transactions += len(txs)
                            logger.info(f"  Account {acc_id}: {len(txs)} transactions")
                        except Exception as e:
                            logger.warning(f"  Account {acc_id}: error: {e}")

                    # ── Fetch Investments ──────────────────────
                    try:
                        investments = client.list_investments(item_id)
                        for inv in investments:
                            upsert_investment(conn, inv, item_id)
                        total_investments += len(investments)
                        logger.info(f"  Found {len(investments)} investments")
                    except Exception as e:
                        logger.warning(f"  Error fetching investments: {e}")

                result.accounts_count = total_accounts
                result.transactions_count = total_transactions
                result.investments_count = total_investments

                log_sync(conn, status="SUCCESS", items_count=result.items_count,
                    accounts_count=result.accounts_count,
                    transactions_count=result.transactions_count,
                    investments_count=result.investments_count)

            result.success = True
            logger.info(f"Sync done: {total_accounts} accounts, {total_transactions} transactions")

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        result.success = False
        result.error_message = str(e)

        try:
            with get_db() as conn:
                log_sync(conn, status="ERROR", error_message=str(e))
        except Exception:
            pass

    return result


def get_sync_status() -> dict:
    """Get current sync status information."""
    last_sync = get_last_sync()
    now = datetime.now()

    if last_sync:
        last_sync_at = datetime.fromisoformat(last_sync["synced_at"])
        hours_since = (now - last_sync_at).total_seconds() / 3600
    else:
        hours_since = None

    return {
        "last_sync": last_sync,
        "hours_since_last_sync": hours_since,
        "is_due": hours_since is not None and hours_since >= DEFAULT_SYNC_INTERVAL_HOURS
        if hours_since is not None
        else True,
        "sync_interval_hours": DEFAULT_SYNC_INTERVAL_HOURS,
    }


# ── Background Sync Task ─────────────────────────────────────────

_sync_lock = asyncio.Lock()
_background_task: Optional[asyncio.Task] = None


async def run_async_sync(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> SyncResult:
    """Run sync in a thread (since httpx is synchronous)."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_sync, lookback_days)
    return result


async def background_sync_loop(interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS):
    """
    Background loop that runs sync periodically.
    Only runs one sync at a time.
    """
    global _sync_lock

    while True:
        try:
            async with _sync_lock:
                logger.info("Background sync: starting...")
                result = await run_async_sync()
                if result.success:
                    logger.info(
                        f"Background sync: OK ({result.accounts_count} accounts, "
                        f"{result.transactions_count} transactions)"
                    )
                else:
                    logger.warning(
                        f"Background sync: FAILED - {result.error_message}"
                    )
        except Exception as e:
            logger.error(f"Background sync: error - {e}")

        await asyncio.sleep(interval_hours * 3600)


def start_background_sync(interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS) -> asyncio.Task:
    """Start the background sync loop. Returns the Task."""
    global _background_task

    if _background_task and not _background_task.done():
        _background_task.cancel()

    _background_task = asyncio.create_task(background_sync_loop(interval_hours))
    return _background_task


def stop_background_sync():
    """Stop the background sync loop."""
    global _background_task
    if _background_task and not _background_task.done():
        _background_task.cancel()
        _background_task = None