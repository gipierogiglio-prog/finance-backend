"""
Pydantic models for API request/response schemas.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Account Models ───────────────────────────────────────────────

class AccountResponse(BaseModel):
    id: str
    name: str
    type: str
    subtype: str
    balance: float
    currency: str = "BRL"
    item_id: str
    credit_limit: Optional[float] = None
    credit_available: Optional[float] = None
    credit_due_date: Optional[str] = None
    overdraft_limit: Optional[float] = None
    synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    total_balance: float
    total_count: int


# ── Transaction Models ───────────────────────────────────────────

class TransactionResponse(BaseModel):
    id: str
    account_id: str
    account_name: Optional[str] = None
    date: date
    description: Optional[str] = None
    amount: float
    type: str  # CREDIT or DEBIT
    category: Optional[str] = None
    category_id: Optional[str] = None
    status: str  # POSTED or PENDING
    currency: str = "BRL"
    payment_method: Optional[str] = None


class TransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    total_count: int
    total_pages: int = 1
    page: int = 1
    page_size: int = 50


class TransactionSummaryResponse(BaseModel):
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_income: float = 0
    total_expenses: float = 0
    net_balance: float = 0
    transaction_count: int = 0


# ── Category Models ──────────────────────────────────────────────

class CategorySpending(BaseModel):
    category: str
    total_amount: float
    transaction_count: int
    percentage: Optional[float] = None


class CategoryListResponse(BaseModel):
    categories: list[CategorySpending]
    total_expenses: float
    period_start: Optional[date] = None
    period_end: Optional[date] = None


# ── Investment Models ────────────────────────────────────────────

class InvestmentResponse(BaseModel):
    id: str
    item_id: str
    name: str
    type: str
    subtype: Optional[str] = None
    balance: float
    amount: Optional[float] = None
    amount_profit: Optional[float] = None
    amount_original: Optional[float] = None
    currency: str = "BRL"
    code: Optional[str] = None
    date: Optional[str] = None
    status: Optional[str] = None


class InvestmentListResponse(BaseModel):
    investments: list[InvestmentResponse]
    total_balance: float
    total_count: int


# ── Sync Models ──────────────────────────────────────────────────

class SyncLogResponse(BaseModel):
    id: int
    synced_at: datetime
    status: str
    items_count: int = 0
    accounts_count: int = 0
    transactions_count: int = 0
    investments_count: int = 0
    error_message: Optional[str] = None


class SyncStatusResponse(BaseModel):
    last_sync: Optional[SyncLogResponse] = None
    hours_since_last_sync: Optional[float] = None
    is_due: bool = True
    sync_interval_hours: int = 6


class SyncTriggerResponse(BaseModel):
    message: str
    sync_result: Optional[dict] = None


# ── System Status ────────────────────────────────────────────────

# ── User / Auth Models ────────────────────────────────────────

class UserResponse(BaseModel):
    id: int
    username: str
    display_name: str = ""


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class PluggyConfigRequest(BaseModel):
    client_id: str
    client_secret: str


class PluggyConfigResponse(BaseModel):
    configured: bool
    has_item: bool


class PluggyConfigInfo(BaseModel):
    client_id: str = ""
    configured: bool = False
    has_item: bool = False


class RegisterResponse(BaseModel):
    message: str
    user: UserResponse


# ── Item (Pluggy Connection) Models ─────────────────────────────

class AddItemRequest(BaseModel):
    item_id: str
    name: Optional[str] = None


class ItemResponse(BaseModel):
    id: str
    item_id: str
    name: str
    status: str
    created_at: datetime


class ItemListResponse(BaseModel):
    items: list[ItemResponse]


# ── System Status ────────────────────────────────────────────────

class SystemStatusResponse(BaseModel):
    status: str = "ok"
    database: str
    pluggy_configured: bool
    last_sync: Optional[SyncLogResponse] = None
    background_sync_running: bool
    accounts_count: int = 0
    transactions_count: int = 0
    investments_count: int = 0