"""
SQLite Database for Polymarket Frontrun Bot.
Persists trades and daily stats for analysis and recovery.
"""

import sqlite3
import logging
from datetime import datetime, date
from typing import List, Optional
from pathlib import Path
from dataclasses import asdict

logger = logging.getLogger(__name__)


class Database:
    """
    SQLite persistence layer for trades and statistics.

    Features:
    - Trade history persistence
    - Daily stats aggregation
    - Automatic table creation
    - Thread-safe connections
    """

    def __init__(self, db_path: str = "trades.db"):
        self.db_path = Path(db_path)
        self._init_tables()
        logger.info(f"Database initialized: {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a new connection (thread-safe pattern)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    pnl REAL NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
                CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);

                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    gross_profit REAL DEFAULT 0.0,
                    gross_loss REAL DEFAULT 0.0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def save_trade(self, trade) -> int:
        """
        Save a trade record to the database.

        Args:
            trade: TradeRecord dataclass

        Returns:
            ID of the inserted trade
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO trades (timestamp, market, side, size, entry_price, exit_price, pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.timestamp.isoformat(),
                trade.market,
                trade.side,
                trade.size,
                trade.entry_price,
                trade.exit_price,
                trade.pnl
            ))
            conn.commit()

            # Update daily stats
            self._update_daily_stats(conn, trade)

            logger.debug(f"Trade saved: ID={cursor.lastrowid}, PnL=${trade.pnl:.2f}")
            return cursor.lastrowid

    def _update_daily_stats(self, conn: sqlite3.Connection, trade):
        """Update daily stats atomically."""
        trade_date = trade.timestamp.date().isoformat()

        # Upsert daily stats
        if trade.pnl >= 0:
            conn.execute("""
                INSERT INTO daily_stats (date, trades, wins, gross_profit)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trades = trades + 1,
                    wins = wins + 1,
                    gross_profit = gross_profit + ?,
                    updated_at = CURRENT_TIMESTAMP
            """, (trade_date, trade.pnl, trade.pnl))
        else:
            conn.execute("""
                INSERT INTO daily_stats (date, trades, losses, gross_loss)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trades = trades + 1,
                    losses = losses + 1,
                    gross_loss = gross_loss + ?,
                    updated_at = CURRENT_TIMESTAMP
            """, (trade_date, abs(trade.pnl), abs(trade.pnl)))

        conn.commit()

    def get_trades(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """
        Get recent trades.

        Args:
            limit: Maximum number of trades to return
            offset: Number of trades to skip

        Returns:
            List of trade dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM trades
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))

            return [dict(row) for row in cursor.fetchall()]

    def get_trades_by_date(self, target_date: date) -> List[dict]:
        """Get all trades for a specific date."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM trades
                WHERE date(timestamp) = ?
                ORDER BY timestamp DESC
            """, (target_date.isoformat(),))

            return [dict(row) for row in cursor.fetchall()]

    def get_daily_stats(self, target_date: Optional[date] = None) -> Optional[dict]:
        """
        Get daily statistics.

        Args:
            target_date: Date to get stats for (defaults to today)

        Returns:
            Dictionary with daily stats or None
        """
        if target_date is None:
            target_date = date.today()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM daily_stats WHERE date = ?
            """, (target_date.isoformat(),))

            row = cursor.fetchone()
            if row:
                stats = dict(row)
                stats['net_pnl'] = stats['gross_profit'] - stats['gross_loss']
                stats['win_rate'] = stats['wins'] / stats['trades'] if stats['trades'] > 0 else 0
                return stats

            return None

    def get_all_time_stats(self) -> dict:
        """Get aggregated all-time statistics."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl >= 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN pnl >= 0 THEN pnl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as gross_loss,
                    SUM(pnl) as net_pnl
                FROM trades
            """)

            row = cursor.fetchone()
            if row:
                stats = dict(row)
                stats['win_rate'] = stats['wins'] / stats['total_trades'] if stats['total_trades'] > 0 else 0
                return stats

            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'gross_profit': 0.0,
                'gross_loss': 0.0,
                'net_pnl': 0.0,
                'win_rate': 0.0
            }

    def load_trade_history(self, limit: int = 1000) -> List[dict]:
        """Load trade history for RiskManager initialization."""
        return self.get_trades(limit=limit)

    def get_trade_count(self) -> int:
        """Get total number of trades."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM trades")
            return cursor.fetchone()[0]

    def vacuum(self):
        """Optimize database file size."""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
        logger.info("Database vacuumed")
