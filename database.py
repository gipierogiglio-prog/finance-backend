"""
Database module — SQLite setup, models, and helpers.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "finance.db")


def get_connection() -> sqlite3.Connection:
    """Get a raw SQLite connection. Use get_db() context manager for normal usage."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """Context manager for database sessions. Auto-commits on success."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                subtype TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'BRL',
                item_id TEXT NOT NULL,
                credit_limit REAL,
                credit_available REAL,
                credit_due_date TEXT,
                overdraft_limit REAL,
                raw_data TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                date TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('CREDIT', 'DEBIT')),
                category TEXT,
                category_id TEXT,
                status TEXT NOT NULL DEFAULT 'POSTED',
                currency TEXT NOT NULL DEFAULT 'BRL',
                payment_method TEXT,
                raw_data TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS investments (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                subtype TEXT,
                balance REAL NOT NULL DEFAULT 0,
                amount REAL,
                amount_profit REAL,
                amount_original REAL,
                currency TEXT NOT NULL DEFAULT 'BRL',
                code TEXT,
                date TEXT,
                status TEXT,
                raw_data TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TEXT NOT NULL DEFAULT (datetime('now')),
                status TEXT NOT NULL DEFAULT 'SUCCESS',
                items_count INTEGER DEFAULT 0,
                accounts_count INTEGER DEFAULT 0,
                transactions_count INTEGER DEFAULT 0,
                investments_count INTEGER DEFAULT 0,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
            CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);
            CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
            CREATE INDEX IF NOT EXISTS idx_accounts_item_id ON accounts(item_id);
            CREATE INDEX IF NOT EXISTS idx_investments_item_id ON investments(item_id);
            CREATE INDEX IF NOT EXISTS idx_sync_log_synced_at ON sync_log(synced_at);
        """)


def upsert_account(conn: sqlite3.Connection, account: dict) -> str:
    """Insert or update an account. Returns the account id."""
    account_id = account["id"]
    raw = json.dumps(account, ensure_ascii=False, default=str)
    
    credit_data = account.get("creditData") or {}
    bank_data = account.get("bankData") or {}
    
    conn.execute("""
        INSERT INTO accounts (id, name, type, subtype, balance, currency, item_id,
                              credit_limit, credit_available, credit_due_date,
                              overdraft_limit, raw_data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            subtype = excluded.subtype,
            balance = excluded.balance,
            currency = excluded.currency,
            item_id = excluded.item_id,
            credit_limit = excluded.credit_limit,
            credit_available = excluded.credit_available,
            credit_due_date = excluded.credit_due_date,
            overdraft_limit = excluded.overdraft_limit,
            raw_data = excluded.raw_data,
            updated_at = excluded.updated_at
    """, (
        account_id,
        account.get("name", ""),
        account.get("type", ""),
        account.get("subtype", ""),
        account.get("balance", 0),
        account.get("currencyCode", "BRL"),
        account.get("itemId", ""),
        credit_data.get("creditLimit"),
        credit_data.get("availableCreditLimit"),
        credit_data.get("balanceDueDate", "")[:10] if credit_data.get("balanceDueDate") else None,
        bank_data.get("overdraftContractedLimit") if bank_data else None,
        raw,
    ))
    return account_id


def upsert_transaction(conn: sqlite3.Connection, tx: dict, account_id: str) -> str:
    """Insert or update a transaction. Returns the transaction id."""
    tx_id = tx["id"]
    raw = json.dumps(tx, ensure_ascii=False, default=str)
    payment_data = tx.get("paymentData") or {}
    
    conn.execute("""
        INSERT INTO transactions (id, account_id, date, description, amount, type,
                                  category, category_id, status, currency,
                                  payment_method, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            account_id = excluded.account_id,
            date = excluded.date,
            description = excluded.description,
            amount = excluded.amount,
            type = excluded.type,
            category = excluded.category,
            category_id = excluded.category_id,
            status = excluded.status,
            currency = excluded.currency,
            payment_method = excluded.payment_method,
            raw_data = excluded.raw_data
    """, (
        tx_id,
        account_id,
        tx.get("date", "")[:10],
        tx.get("description", ""),
        tx.get("amount", 0),
        tx.get("type", "DEBIT"),
        tx.get("category", ""),
        tx.get("categoryId", ""),
        tx.get("status", "POSTED"),
        tx.get("currencyCode", "BRL"),
        payment_data.get("paymentMethod"),
        raw,
    ))
    return tx_id


def upsert_investment(conn: sqlite3.Connection, inv: dict, item_id: str) -> str:
    """Insert or update an investment. Returns the investment id."""
    inv_id = inv["id"]
    raw = json.dumps(inv, ensure_ascii=False, default=str)
    
    conn.execute("""
        INSERT INTO investments (id, item_id, name, type, subtype, balance, amount,
                                 amount_profit, amount_original, currency, code,
                                 date, status, raw_data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            item_id = excluded.item_id,
            name = excluded.name,
            type = excluded.type,
            subtype = excluded.subtype,
            balance = excluded.balance,
            amount = excluded.amount,
            amount_profit = excluded.amount_profit,
            amount_original = excluded.amount_original,
            currency = excluded.currency,
            code = excluded.code,
            date = excluded.date,
            status = excluded.status,
            raw_data = excluded.raw_data,
            updated_at = excluded.updated_at
    """, (
        inv_id,
        item_id,
        inv.get("name", ""),
        inv.get("type", ""),
        inv.get("subtype"),
        inv.get("balance", 0),
        inv.get("amount"),
        inv.get("amountProfit"),
        inv.get("amountOriginal"),
        inv.get("currencyCode", "BRL"),
        inv.get("code"),
        inv.get("date", "")[:10] if inv.get("date") else None,
        inv.get("status"),
        raw,
    ))
    return inv_id


def log_sync(
    conn: sqlite3.Connection,
    status: str = "SUCCESS",
    items_count: int = 0,
    accounts_count: int = 0,
    transactions_count: int = 0,
    investments_count: int = 0,
    error_message: Optional[str] = None,
) -> int:
    """Insert a sync log entry. Returns the log id."""
    cursor = conn.execute("""
        INSERT INTO sync_log (status, items_count, accounts_count,
                              transactions_count, investments_count, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (status, items_count, accounts_count, transactions_count, investments_count, error_message))
    return cursor.lastrowid


def get_last_sync() -> Optional[dict]:
    """Get the most recent sync log entry."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        return None