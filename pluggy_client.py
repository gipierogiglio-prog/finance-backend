"""
Pluggy API client — reusable HTTP client for the Pluggy/MeuPluggy API.

Reuses authentication and endpoint logic from the existing pluggy_sync.py script.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
from typing import Optional

import httpx

PLUGGY_BASE_URL = "https://api.pluggy.ai"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pluggy-items.json")

# Rate limiting: max 10 req/s as per API docs
RATE_LIMIT_DELAY = 0.15  # ~6 req/s to be safe


class PluggyError(Exception):
    """Base exception for Pluggy API errors."""
    pass


class PluggyAuthError(PluggyError):
    """Authentication error (invalid credentials or expired token)."""
    pass


class PluggyRateLimitError(PluggyError):
    """Rate limit exceeded."""
    pass


class PluggyItemNotReadyError(PluggyError):
    """Item is not ready (WAITING_USER_INPUT, LOGIN_ERROR, etc.)."""
    pass


class PluggyClient:
    """HTTP client for the Pluggy API with auto-refresh auth."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._api_key: Optional[str] = None
        self._client = httpx.Client(timeout=30.0, base_url=PLUGGY_BASE_URL)

    def _get_api_key(self) -> str:
        """Authenticate and return a fresh API key (JWT)."""
        resp = self._client.post("/auth", json={
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
        })
        if resp.status_code == 401:
            raise PluggyAuthError("Invalid client credentials")
        resp.raise_for_status()
        return resp.json()["apiKey"]

    def _ensure_auth(self):
        """Ensure we have a valid API key."""
        if not self._api_key:
            self._api_key = self._get_api_key()

    def _headers(self) -> dict:
        return {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with automatic retry on 403/429."""
        self._ensure_auth()
        headers = self._headers()

        max_retries = 3
        for attempt in range(max_retries):
            resp = self._client.request(method, path, headers=headers, **kwargs)

            if resp.status_code == 403:
                # Token expired — refresh and retry once
                self._api_key = self._get_api_key()
                headers = self._headers()
                resp = self._client.request(method, path, headers=headers, **kwargs)
                if resp.status_code == 403:
                    raise PluggyAuthError("Authentication failed after token refresh")
                return resp

            if resp.status_code == 429:
                wait = min(2 ** attempt * 2, 30)
                time.sleep(wait)
                continue

            if resp.status_code == 404:
                # Let the caller handle 404s
                return resp

            resp.raise_for_status()
            return resp

        raise PluggyRateLimitError("Rate limit exceeded after retries")

    def _get(self, path: str, params: dict = None) -> dict:
        """GET request returning parsed JSON."""
        time.sleep(RATE_LIMIT_DELAY)
        resp = self._request("GET", path, params=params)
        return resp.json()

    def _post(self, path: str, json_data: dict = None) -> dict:
        """POST request returning parsed JSON."""
        time.sleep(RATE_LIMIT_DELAY)
        resp = self._request("POST", path, json=json_data)
        return resp.json()

    # ── Item Management ──────────────────────────────────────────

    def load_saved_items(self) -> list:
        """Load saved Item IDs from local JSON file."""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                data = json.load(f)
                return data.get("items", [])
        return []

    def save_items(self, items: list):
        """Save Item IDs to local JSON file."""
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "items": items,
                "updated_at": datetime.now().isoformat(),
            }, f, indent=2)

    def list_items(self) -> list:
        """List all Items. Falls back to saved items if GET /items fails."""
        resp = self._client.get(
            "/items",
            headers=self._headers() if self._api_key else {"X-API-KEY": self._get_api_key(), "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                self.save_items([
                    {"id": i["id"], "connector": i.get("connector", {}).get("name")}
                    for i in results
                ])
            return results

        # Fallback: use saved items
        saved = self.load_saved_items()
        if saved:
            items = []
            for s in saved:
                try:
                    item = self.get_item(s["id"])
                    items.append(item)
                except Exception:
                    pass
            return items

        return []

    def get_item(self, item_id: str) -> dict:
        """Get a single Item by ID."""
        return self._get(f"/items/{item_id}")

    def create_item(self, connector_id: int = 200, parameters: dict = None) -> dict:
        """Create a new Item (connector connection)."""
        payload = {"connectorId": connector_id, "parameters": parameters or {}}
        item = self._post("/items", json_data=payload)

        # Save the item id locally regardless of status
        saved = self.load_saved_items()
        saved.append({
            "id": item["id"],
            "connector": "MeuPluggy",
        })
        self.save_items(saved)

        return item

    def ensure_item(self) -> dict:
        """
        Ensure we have a working Item.
        Returns the item dict. Raises PluggyItemNotReadyError if OAuth is needed.
        """
        # First try ITEM_ID from env
        env_item_id = os.environ.get("PLUGGY_ITEM_ID")
        if env_item_id:
            try:
                item = self.get_item(env_item_id)
                status = item.get("status")
                if status == "UPDATED":
                    logger.info(f"Using Item from env: {env_item_id}")
                    return item
                if status == "WAITING_USER_INPUT":
                    params = item.get("parameter") or {}
                    logger.warning(f"Item {env_item_id} from env needs OAuth")
                    raise PluggyItemNotReadyError(
                        f"Item {env_item_id} needs OAuth authorization. "
                        f"Open: {params.get('data', 'N/A')}"
                    )
            except Exception as e:
                logger.warning(f"Item from env failed: {e}")

        # Then try saved items
        saved = self.load_saved_items()
        for s in saved:
            try:
                item = self.get_item(s["id"])
                status = item.get("status")
                if status == "UPDATED":
                    return item
                if status == "WAITING_USER_INPUT":
                    params = item.get("parameter") or {}
                    raise PluggyItemNotReadyError(
                        f"Item {item['id']} needs OAuth authorization. "
                        f"Open this URL: {params.get('data', 'N/A')}"
                    )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue  # Item was deleted, try next
                raise

        # Create a new item
        item = self.create_item(connector_id=200)
        status = item.get("status")
        if status == "WAITING_USER_INPUT":
            params = item.get("parameter") or {}
            raise PluggyItemNotReadyError(
                f"New Item {item['id']} created but needs OAuth authorization. "
                f"Open this URL: {params.get('data', 'N/A')}"
            )

        return item

    def ensure_all_items(self) -> list:
        """
        Return all saved Items.
        Skips items that are WAITING_USER_INPUT or deleted.
        """
        saved = self.load_saved_items()
        items = []
        for s in saved:
            try:
                item = self.get_item(s["id"])
                status = item.get("status")
                if status == "UPDATED":
                    items.append(item)
                elif status == "WAITING_USER_INPUT":
                    logger.warning(f"Item {s['id']} needs OAuth, skipping")
                else:
                    logger.info(f"Item {s['id']}: status={status}, skipping")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Item {s['id']} not found, skipping")
                else:
                    logger.warning(f"Item {s['id']}: error {e}, skipping")
        return items

    # ── Data Fetching ────────────────────────────────────────────

    def list_accounts(self, item_id: str) -> list:
        """List all accounts for an Item."""
        return self._get("/accounts", params={"itemId": item_id}).get("results", [])

    def get_account(self, account_id: str) -> dict:
        """Get a single account by ID."""
        return self._get(f"/accounts/{account_id}")

    def get_transactions(
        self,
        account_id: str,
        date_from: str,
        date_to: str,
    ) -> list:
        """
        Get all transactions for an account in a date range.
        Uses cursor-based pagination (v2).
        """
        all_tx = []
        after = None

        while True:
            params = {
                "accountId": account_id,
                "dateFrom": date_from,
                "dateTo": date_to,
            }
            if after:
                params["after"] = after

            data = self._get("/v2/transactions", params=params)
            all_tx.extend(data.get("results", []))

            next_cursor = data.get("next")
            if not next_cursor:
                break
            after = next_cursor

        return all_tx

    def list_investments(self, item_id: str) -> list:
        """List all investments for an Item."""
        return self._get("/investments", params={"itemId": item_id}).get("results", [])

    def list_bills(self, account_id: str) -> list:
        """List credit card bills for an account."""
        return self._get("/bills", params={"accountId": account_id}).get("results", [])

    def get_identity(self, item_id: str) -> dict:
        """Get identity data for an Item."""
        return self._get("/identity", params={"itemId": item_id})

    def get_account_balance(self, account_id: str) -> dict:
        """Get real-time balance for an account."""
        return self._get(f"/accounts/{account_id}/balance")

    # ── Cleanup ──────────────────────────────────────────────────

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()