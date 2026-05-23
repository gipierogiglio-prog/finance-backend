"""
Sync service — orchestrates data sync from Pluggy to local database.
Supports per-user sync with credentials stored in the users table.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from database import (
    get_db,
    get_user,
    init_db,
    upsert_account,
    upsert_transaction,
    upsert_investment,
    log_sync,
    get_last_sync,
)
from pluggy_client import PluggyClient, PluggyItemNotReadyError, PluggyNotConfiguredError

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


def get_user_pluggy_credentials(user_id: int) -> dict:
    """Get Pluggy credentials and items for a user from the database."""
    with get_db() as conn:
        user = get_user(conn, user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        client_id = (user.get("pluggy_client_id") or "").strip()
        client_secret = (user.get("pluggy_client_secret") or "").strip()

        if not client_id or not client_secret:
            raise PluggyNotConfiguredError(
                "Configure seu MeuPluggy no dashboard (PUT /api/user/pluggy-config)"
            )

        items_raw = user.get("pluggy_items") or "[]"
        if isinstance(items_raw, str):
            items = json.loads(items_raw)
        else:
            items = items_raw

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "items": items,
        }


def save_user_items(user_id: int, items: list):
    """Save items list to the user's pluggy_items field."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET pluggy_items = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(items, ensure_ascii=False), user_id),
        )


def merge_new_item(user_id: int, new_item: dict):
    """Add or update an item in the user's items list."""
    items_raw = None
    with get_db() as conn:
        user = get_user(conn, user_id)
        if user:
            raw = user.get("pluggy_items") or "[]"
            if isinstance(raw, str):
                items_raw = json.loads(raw)
            else:
                items_raw = raw

    if items_raw is None:
        items_raw = []

    # Check if item already exists
    found = False
    for i, existing in enumerate(items_raw):
        if existing.get("id") == new_item.get("id"):
            items_raw[i] = {
                "id": new_item["id"],
                "connector": new_item.get("connector", {}).get("name", "MeuPluggy"),
            }
            found = True
            break

    if not found:
        items_raw.append({
            "id": new_item["id"],
            "connector": new_item.get("connector", {}).get("name", "MeuPluggy"),
        })

    save_user_items(user_id, items_raw)


def run_sync(user_id: int = 1, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> SyncResult:
    """
    Run a full sync for a specific user: fetch accounts, transactions, investments
    from Pluggy and save to local SQLite database.

    This is a synchronous function. Run in a thread if called from async context.
    """
    logger.info(f"Starting sync for user_id={user_id}...")
    result = SyncResult()

    try:
        # Ensure database tables exist
        init_db()

        # Get user's Pluggy credentials
        creds = get_user_pluggy_credentials(user_id)
        client_id = creds["client_id"]
        client_secret = creds["client_secret"]
        saved_items = creds["items"]

        logger.info(f"User {user_id}: using Pluggy client_id={client_id[:8]}...")

        with PluggyClient(client_id=client_id, client_secret=client_secret) as client:
            # ── Get all Items ────────────────────────────────
            items = client.ensure_all_items(saved_items)

            if not items:
                logger.warning(f"User {user_id}: no working items found")
                result.success = False
                result.error_message = "Nenhum Item disponível para sincronia. Adicione um Item ID no dashboard."
                with get_db() as conn:
                    log_sync(conn, status="NO_ITEMS", error_message="Nenhum Item ativo encontrado", user_id=user_id)
                return result

            result.items_count = len(items)
            logger.info(f"User {user_id}: using {len(items)} Item(s)")

            total_transactions = 0
            total_accounts = 0
            total_investments = 0

            date_to = datetime.now().strftime("%Y-%m-%d")
            date_from = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

            with get_db() as conn:
                for item in items:
                    item_id = item["id"]
                    logger.info(f"User {user_id}: processing Item: {item_id}")

                    # ── Fetch Accounts ─────────────────────────
                    try:
                        accounts = client.list_accounts(item_id)
                        logger.info(f"  Found {len(accounts)} accounts")
                        for account in accounts:
                            upsert_account(conn, account, user_id=user_id)
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
                            upsert_investment(conn, inv, item_id, user_id=user_id)
                        total_investments += len(investments)
                        logger.info(f"  Found {len(investments)} investments")
                    except Exception as e:
                        logger.warning(f"  Error fetching investments: {e}")

                result.accounts_count = total_accounts
                result.transactions_count = total_transactions
                result.investments_count = total_investments

                log_sync(
                    conn, status="SUCCESS", items_count=result.items_count,
                    accounts_count=result.accounts_count,
                    transactions_count=result.transactions_count,
                    investments_count=result.investments_count,
                    user_id=user_id,
                )

            result.success = True
            logger.info(f"User {user_id}: sync done: {total_accounts} accounts, {total_transactions} transactions")

    except PluggyNotConfiguredError as e:
        logger.warning(f"User {user_id}: Pluggy not configured: {e}")
        result.success = False
        result.error_message = str(e)
        try:
            with get_db() as conn:
                log_sync(conn, status="NOT_CONFIGURED", error_message=str(e), user_id=user_id)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"User {user_id}: sync failed: {e}")
        result.success = False
        result.error_message = str(e)

        try:
            with get_db() as conn:
                log_sync(conn, status="ERROR", error_message=str(e), user_id=user_id)
        except Exception:
            pass

    return result


def remove_user_item(user_id: int, item_id: str):
    """Remove an item from the user's pluggy_items list."""
    with get_db() as conn:
        user = get_user(conn, user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        items_raw = user.get("pluggy_items") or "[]"
        if isinstance(items_raw, str):
            items = json.loads(items_raw)
        else:
            items = items_raw

        items = [i for i in items if i.get("id") != item_id]
        save_user_items(user_id, items)


def get_sync_status(user_id: int = 1) -> dict:
    """Get current sync status information for a user."""
    last_sync = get_last_sync(user_id)
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


def run_sync_all_users(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list:
    """
    Run sync for ALL users who have Pluggy configured.
    Returns list of SyncResult dicts.
    """
    results = []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM users WHERE pluggy_client_id != '' AND pluggy_client_secret != ''"
        ).fetchall()
        user_ids = [r["id"] for r in rows]

    logger.info(f"Running sync for {len(user_ids)} user(s) with Pluggy configured")

    for uid in user_ids:
        try:
            res = run_sync(user_id=uid, lookback_days=lookback_days)
            results.append({"user_id": uid, "result": res.to_dict()})
        except Exception as e:
            logger.error(f"Sync failed for user {uid}: {e}")
            results.append({"user_id": uid, "result": {"success": False, "error_message": str(e)}})

    return results


# ── Background Sync Task ─────────────────────────────────────────

_sync_lock = asyncio.Lock()
_background_task: Optional[asyncio.Task] = None


async def run_async_sync(user_id: int = 1, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> SyncResult:
    """Run sync in a thread (since httpx is synchronous)."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_sync, user_id, lookback_days)
    return result


async def background_sync_loop(interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS):
    """
    Background loop that runs sync for ALL users periodically.
    Only runs one sync at a time.
    """
    global _sync_lock

    while True:
        try:
            async with _sync_lock:
                logger.info("Background sync: starting for all users...")
                results = await asyncio.get_running_loop().run_in_executor(
                    None, run_sync_all_users
                )
                success_count = sum(
                    1 for r in results if r.get("result", {}).get("success")
                )
                logger.info(
                    f"Background sync: {success_count}/{len(results)} users OK"
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