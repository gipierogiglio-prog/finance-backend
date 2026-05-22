"""
Finance Backend — FastAPI application.

API endpoints for the Garrinha Finance Dashboard.
Consumes Pluggy/MeuPluggy Open Finance data and exposes it via REST.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from auth import (
    LoginRequest,
    LoginResponse,
    create_access_token,
    verify_credentials,
    get_current_user,
)
from database import get_db, get_last_sync, init_db
from models import (
    AccountListResponse,
    AccountResponse,
    CategoryListResponse,
    CategorySpending,
    InvestmentListResponse,
    InvestmentResponse,
    SyncLogResponse,
    SyncStatusResponse,
    SyncTriggerResponse,
    SystemStatusResponse,
    TransactionListResponse,
    TransactionResponse,
    TransactionSummaryResponse,
)
from sync_service import (
    DEFAULT_SYNC_INTERVAL_HOURS,
    run_async_sync,
    start_background_sync,
    stop_background_sync,
    get_sync_status,
)

# ── Logger ───────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Lifecycle ────────────────────────────────────────────────────
_background_task_flag = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize DB and start background sync."""
    global _background_task_flag

    # Ensure DB exists
    init_db()
    logger.info("Database initialized")

    # Start background sync if auto-sync is enabled
    auto_sync = os.getenv("AUTO_SYNC", "true").lower() == "true"
    if auto_sync:
        start_background_sync()
        _background_task_flag = True
        logger.info("Background sync started")

    yield

    # Cleanup
    stop_background_sync()
    logger.info("Background sync stopped")


# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="Garrinha Finance API v2",
    description="Backend financeiro para dashboard pessoal — dados via Pluggy/MeuPluggy (Open Finance Brasil)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow localhost:5173 (Vite dev server) and any production URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "https://financeiro.devgiglio.uk",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health / Status ──────────────────────────────────────────────

@app.get("/api/status", response_model=SystemStatusResponse)
async def get_status():
    """System status, database info, and last sync details."""
    last_sync = get_last_sync()
    last_sync_model = None
    if last_sync:
        last_sync_model = SyncLogResponse(**last_sync)

    with get_db() as conn:
        accounts_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        transactions_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        investments_count = conn.execute("SELECT COUNT(*) FROM investments").fetchone()[0]

    pluggy_configured = bool(
        os.getenv("PLUGGY_CLIENT_ID") and os.getenv("PLUGGY_CLIENT_SECRET")
    )

    return SystemStatusResponse(
        status="ok",
        database="sqlite",
        pluggy_configured=pluggy_configured,
        last_sync=last_sync_model,
        background_sync_running=_background_task_flag,
        accounts_count=accounts_count,
        transactions_count=transactions_count,
        investments_count=investments_count,
    )


# ── Auth ──────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate and receive a JWT Bearer token."""
    if not verify_credentials(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha inválidos",
        )

    token = create_access_token(body.username)
    return LoginResponse(access_token=token)


# ── Sync ─────────────────────────────────────────────────────────

@app.post("/api/sync", response_model=SyncTriggerResponse)
async def trigger_sync(
    _user: str = Depends(get_current_user),lookback_days: int = Query(90, description="Days of transactions to fetch")):
    """Manually trigger a full sync with Pluggy."""
    try:
        result = await run_async_sync(lookback_days=lookback_days)

        if result.oauth_url:
            return SyncTriggerResponse(
                message="Authorization needed. Open the URL to connect MeuPluggy.",
                sync_result=result.to_dict(),
            )

        if result.success:
            return SyncTriggerResponse(
                message=(
                    f"Sync completed: {result.accounts_count} accounts, "
                    f"{result.transactions_count} transactions, "
                    f"{result.investments_count} investments"
                ),
                sync_result=result.to_dict(),
            )
        else:
            return SyncTriggerResponse(
                message=f"Sync failed: {result.error_message}",
                sync_result=result.to_dict(),
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync/status", response_model=SyncStatusResponse)
async def sync_status(
    _user: str = Depends(get_current_user),
):
    """Get current sync status."""
    status = get_sync_status()
    last_sync = status["last_sync"]
    last_sync_model = SyncLogResponse(**last_sync) if last_sync else None

    return SyncStatusResponse(
        last_sync=last_sync_model,
        hours_since_last_sync=status["hours_since_last_sync"],
        is_due=status["is_due"],
        sync_interval_hours=status["sync_interval_hours"],
    )


# ── Accounts ─────────────────────────────────────────────────────

@app.get("/api/accounts", response_model=AccountListResponse)
async def list_accounts(
    _user: str = Depends(get_current_user),
):
    """List all accounts with balances."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, name, type, subtype, balance, currency, item_id,
                   credit_limit, credit_available, credit_due_date,
                   overdraft_limit, updated_at as synced_at
            FROM accounts
            ORDER BY type, name
        """).fetchall()

        accounts = [AccountResponse(**dict(r)) for r in rows]
        total_balance = sum(
            a.balance for a in accounts if a.type == "BANK"
        )

    return AccountListResponse(
        accounts=accounts,
        total_balance=total_balance,
        total_count=len(accounts),
    )


@app.get("/api/accounts/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: str,
    _user: str = Depends(get_current_user),
):
    """Get details for a specific account."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, type, subtype, balance, currency, item_id, "
            "credit_limit, credit_available, credit_due_date, "
            "overdraft_limit, updated_at as synced_at "
            "FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    return AccountResponse(**dict(row))


# ── Transactions ─────────────────────────────────────────────────

@app.get("/api/transactions", response_model=TransactionListResponse)
async def list_transactions(
    _user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    account_id: Optional[str] = Query(None, description="Filter by account"),
    category: Optional[str] = Query(None, description="Filter by category"),
    type_filter: Optional[str] = Query(None, alias="type", description="Filter by type (CREDIT/DEBIT)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
):
    """List transactions with filters and pagination."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    where_clauses = ["t.date >= ?", "t.date <= ?"]
    params = [date_from, date_to]

    if account_id:
        where_clauses.append("t.account_id = ?")
        params.append(account_id)
    if category:
        where_clauses.append("t.category = ?")
        params.append(category)
    if type_filter:
        where_clauses.append("t.type = ?")
        params.append(type_filter)

    where = " AND ".join(where_clauses)

    with get_db() as conn:
        # Count
        total = conn.execute(
            f"SELECT COUNT(*) FROM transactions t WHERE {where}",
            params,
        ).fetchone()[0]

        # Data with account name join
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT t.id, t.account_id, a.name as account_name,
                   t.date, t.description, t.amount, t.type,
                   t.category, t.category_id, t.status, t.currency,
                   t.payment_method
            FROM transactions t
            LEFT JOIN accounts a ON t.account_id = a.id
            WHERE {where}
            ORDER BY t.date DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ).fetchall()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return TransactionListResponse(
        transactions=[TransactionResponse(**dict(r)) for r in rows],
        total_count=total,
        total_pages=total_pages,
        page=page,
        page_size=page_size,
    )


@app.get("/api/transactions/summary", response_model=TransactionSummaryResponse)
async def transaction_summary(
    _user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    account_id: Optional[str] = Query(None, description="Filter by account"),
):
    """Get income/expense summary for a period."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    where_clauses = ["date >= ?", "date <= ?"]
    params = [date_from, date_to]

    if account_id:
        where_clauses.append("account_id = ?")
        params.append(account_id)

    where = " AND ".join(where_clauses)

    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN type = 'CREDIT' THEN amount ELSE 0 END), 0) as total_income,
                COALESCE(SUM(CASE WHEN type = 'DEBIT' THEN amount ELSE 0 END), 0) as total_expenses,
                COUNT(*) as transaction_count
            FROM transactions
            WHERE {where}
            """,
            params,
        ).fetchone()

    total_income = row["total_income"]
    total_expenses = row["total_expenses"]
    # total_expenses is already negative (DEBITs stored as negative amounts)
    # so we ADD them to get the correct net balance
    net_balance = total_income + total_expenses

    return TransactionSummaryResponse(
        period_start=date.fromisoformat(date_from),
        period_end=date.fromisoformat(date_to),
        total_income=total_income,
        total_expenses=total_expenses,
        net_balance=net_balance,
        transaction_count=row["transaction_count"],
    )


# ── Categories ───────────────────────────────────────────────────

@app.get("/api/categories", response_model=CategoryListResponse)
async def categories_summary(
    _user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    account_id: Optional[str] = Query(None, description="Filter by account"),
):
    """Get expenses aggregated by category."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    where_clauses = ["t.date >= ?", "t.date <= ?"]
    params = [date_from, date_to]

    if account_id:
        where_clauses.append("t.account_id = ?")
        params.append(account_id)

    where = " AND ".join(where_clauses)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(NULLIF(t.category, ''), 'Outros') as category,
                SUM(t.amount) as total_amount,
                COUNT(*) as transaction_count
            FROM transactions t
            WHERE t.type = 'DEBIT' AND {where}
            GROUP BY t.category
            ORDER BY total_amount DESC
            """,
            params,
        ).fetchall()

        total_expenses = sum(r["total_amount"] for r in rows)

        categories = []
        for r in rows:
            total = r["total_amount"]
            pct = (total / total_expenses * 100) if total_expenses > 0 else 0
            categories.append(
                CategorySpending(
                    category=r["category"],
                    total_amount=total,
                    transaction_count=r["transaction_count"],
                    percentage=round(pct, 1),
                )
            )

    return CategoryListResponse(
        categories=categories,
        total_expenses=total_expenses,
        period_start=date.fromisoformat(date_from),
        period_end=date.fromisoformat(date_to),
    )


# ── Investments ──────────────────────────────────────────────────

@app.get("/api/investments", response_model=InvestmentListResponse)
async def list_investments(
    _user: str = Depends(get_current_user),
):
    """List all investments."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, item_id, name, type, subtype, balance, amount,
                   amount_profit, amount_original, currency, code,
                   date, status
            FROM investments
            ORDER BY balance DESC
        """).fetchall()

        investments = [InvestmentResponse(**dict(r)) for r in rows]
        total_balance = sum(i.balance for i in investments)

    return InvestmentListResponse(
        investments=investments,
        total_balance=total_balance,
        total_count=len(investments),
    )


# ── Sync Log History ─────────────────────────────────────────────

@app.get("/api/sync/logs")
async def sync_logs(
    _user: str = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=100),
):
    """Get recent sync log entries."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return {"logs": [dict(r) for r in rows]}


# ── Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )