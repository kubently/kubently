#!/usr/bin/env python3
"""
Whitelist Store for Kubently Agent.

Handles persistence of command history, metrics, and learning data.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("kubently-agent.whitelist-store")


class WhitelistStore:
    """Storage and metrics for dynamic whitelist system."""

    def __init__(self, db_path: str = "/var/lib/kubently/whitelist.db"):
        """
        Initialize whitelist store.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

        # Metrics cache
        self._metrics_cache = {
            "config_reloads": Counter(),
            "command_validations": Counter(),
            "commands_executed": Counter(),
            "commands_blocked": Counter(),
        }

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        try:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, isolation_level=None  # Autocommit mode
            )

            # Enable Write-Ahead Logging for better concurrency
            self._conn.execute("PRAGMA journal_mode=WAL")

            # Create tables
            self._create_tables()

            logger.info(f"Database initialized at {self.db_path}")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _create_tables(self) -> None:
        """Create database tables."""
        with self._lock:
            cursor = self._conn.cursor()

            # Command history table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS command_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    cluster_id TEXT NOT NULL,
                    verb TEXT NOT NULL,
                    full_command TEXT NOT NULL,
                    category TEXT,
                    risk_level TEXT,
                    allowed BOOLEAN NOT NULL,
                    rejection_reason TEXT,
                    execution_time_ms INTEGER,
                    success BOOLEAN,
                    error_message TEXT
                )
            """
            )

            # Create indexes for efficient queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_command_timestamp 
                ON command_history(timestamp DESC)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_command_verb 
                ON command_history(verb)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_command_allowed 
                ON command_history(allowed)
            """
            )

            # Configuration history table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS config_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    config_hash TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    allowed_verbs TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    error_message TEXT
                )
            """
            )

            # Learning patterns table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT UNIQUE NOT NULL,
                    verb TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    occurrence_count INTEGER DEFAULT 1,
                    always_allowed BOOLEAN DEFAULT TRUE,
                    risk_assessment TEXT
                )
            """
            )

            # Metrics table for aggregated data
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    labels TEXT
                )
            """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp 
                ON metrics(timestamp DESC)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metrics_name 
                ON metrics(metric_name)
            """
            )

    def record_command(
        self,
        cluster_id: str,
        args: List[str],
        allowed: bool,
        rejection_reason: Optional[str] = None,
        category: Optional[str] = None,
        risk_level: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        success: Optional[bool] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Record command execution attempt.

        Args:
            cluster_id: Cluster identifier
            args: Command arguments
            allowed: Whether command was allowed
            rejection_reason: Reason if rejected
            category: Command category
            risk_level: Risk assessment
            execution_time_ms: Execution time in milliseconds
            success: Whether execution succeeded
            error_message: Error if execution failed
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                verb = args[0] if args else ""
                full_command = " ".join(args)

                cursor.execute(
                    """
                    INSERT INTO command_history 
                    (timestamp, cluster_id, verb, full_command, category, risk_level,
                     allowed, rejection_reason, execution_time_ms, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        time.time(),
                        cluster_id,
                        verb,
                        full_command,
                        category,
                        risk_level,
                        allowed,
                        rejection_reason,
                        execution_time_ms,
                        success,
                        error_message,
                    ),
                )

                # Update metrics cache
                if allowed:
                    self._metrics_cache["commands_executed"][verb] += 1
                else:
                    self._metrics_cache["commands_blocked"][rejection_reason or "unknown"] += 1

            except Exception as e:
                logger.error(f"Failed to record command: {e}")

    def record_config_reload(
        self,
        config_hash: str,
        mode: str,
        allowed_verbs: List[str],
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Record configuration reload event.

        Args:
            config_hash: Hash of configuration
            mode: Security mode
            allowed_verbs: List of allowed verbs
            success: Whether reload succeeded
            error_message: Error if reload failed
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO config_history 
                    (timestamp, config_hash, mode, allowed_verbs, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        time.time(),
                        config_hash,
                        mode,
                        json.dumps(allowed_verbs),
                        success,
                        error_message,
                    ),
                )

                # Update metrics cache
                status = "success" if success else "failure"
                self._metrics_cache["config_reloads"][status] += 1

            except Exception as e:
                logger.error(f"Failed to record config reload: {e}")

    def record_pattern(
        self, pattern: str, verb: str, allowed: bool, risk_assessment: Optional[str] = None
    ) -> None:
        """
        Record command pattern for learning.

        Args:
            pattern: Command pattern
            verb: Command verb
            allowed: Whether pattern was allowed
            risk_assessment: Risk assessment
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                now = time.time()

                # Try to update existing pattern
                cursor.execute(
                    """
                    UPDATE learning_patterns 
                    SET last_seen = ?, 
                        occurrence_count = occurrence_count + 1,
                        always_allowed = always_allowed AND ?
                    WHERE pattern = ?
                """,
                    (now, allowed, pattern),
                )

                if cursor.rowcount == 0:
                    # Insert new pattern
                    cursor.execute(
                        """
                        INSERT INTO learning_patterns 
                        (pattern, verb, first_seen, last_seen, occurrence_count, 
                         always_allowed, risk_assessment)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                        (pattern, verb, now, now, allowed, risk_assessment),
                    )

            except Exception as e:
                logger.error(f"Failed to record pattern: {e}")

    def get_command_stats(
        self, cluster_id: Optional[str] = None, hours: int = 24
    ) -> Dict[str, Any]:
        """
        Get command statistics.

        Args:
            cluster_id: Filter by cluster (optional)
            hours: Time window in hours

        Returns:
            Statistics dictionary
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                since = time.time() - (hours * 3600)

                # Base query
                base_where = "timestamp > ?"
                params = [since]

                if cluster_id:
                    base_where += " AND cluster_id = ?"
                    params.append(cluster_id)

                # Total commands
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM command_history 
                    WHERE {base_where}
                """,
                    params,
                )
                total_commands = cursor.fetchone()[0]

                # Allowed vs blocked
                cursor.execute(
                    f"""
                    SELECT allowed, COUNT(*) FROM command_history 
                    WHERE {base_where}
                    GROUP BY allowed
                """,
                    params,
                )
                allowed_blocked = dict(cursor.fetchall())

                # Top verbs
                cursor.execute(
                    f"""
                    SELECT verb, COUNT(*) as count FROM command_history 
                    WHERE {base_where}
                    GROUP BY verb
                    ORDER BY count DESC
                    LIMIT 10
                """,
                    params,
                )
                top_verbs = cursor.fetchall()

                # Risk distribution
                cursor.execute(
                    f"""
                    SELECT risk_level, COUNT(*) FROM command_history 
                    WHERE {base_where} AND risk_level IS NOT NULL
                    GROUP BY risk_level
                """,
                    params,
                )
                risk_distribution = dict(cursor.fetchall())

                # Rejection reasons
                cursor.execute(
                    f"""
                    SELECT rejection_reason, COUNT(*) as count FROM command_history 
                    WHERE {base_where} AND allowed = 0 AND rejection_reason IS NOT NULL
                    GROUP BY rejection_reason
                    ORDER BY count DESC
                    LIMIT 10
                """,
                    params,
                )
                rejection_reasons = cursor.fetchall()

                return {
                    "time_window_hours": hours,
                    "total_commands": total_commands,
                    "allowed": allowed_blocked.get(1, 0),
                    "blocked": allowed_blocked.get(0, 0),
                    "top_verbs": top_verbs,
                    "risk_distribution": risk_distribution,
                    "top_rejection_reasons": rejection_reasons,
                }

            except Exception as e:
                logger.error(f"Failed to get command stats: {e}")
                return {}

    def get_learning_suggestions(
        self, min_occurrences: int = 10, min_days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get suggestions for commands to add to whitelist.

        Args:
            min_occurrences: Minimum occurrences to consider
            min_days: Minimum days pattern has been seen

        Returns:
            List of suggestion dictionaries
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                min_age = time.time() - (min_days * 86400)

                cursor.execute(
                    """
                    SELECT pattern, verb, occurrence_count, 
                           first_seen, last_seen, risk_assessment
                    FROM learning_patterns
                    WHERE occurrence_count >= ?
                      AND first_seen <= ?
                      AND always_allowed = 1
                    ORDER BY occurrence_count DESC
                    LIMIT 20
                """,
                    (min_occurrences, min_age),
                )

                suggestions = []
                for row in cursor.fetchall():
                    pattern, verb, count, first_seen, last_seen, risk = row

                    suggestions.append(
                        {
                            "pattern": pattern,
                            "verb": verb,
                            "occurrence_count": count,
                            "first_seen": datetime.fromtimestamp(first_seen).isoformat(),
                            "last_seen": datetime.fromtimestamp(last_seen).isoformat(),
                            "risk_assessment": risk,
                            "days_active": int((last_seen - first_seen) / 86400),
                        }
                    )

                return suggestions

            except Exception as e:
                logger.error(f"Failed to get learning suggestions: {e}")
                return []

    def export_metrics(self) -> Dict[str, Any]:
        """
        Export metrics in Prometheus format.

        Returns:
            Metrics dictionary
        """
        with self._lock:
            try:
                metrics = {
                    "kubently_config_reloads_total": dict(self._metrics_cache["config_reloads"]),
                    "kubently_commands_executed_total": dict(
                        self._metrics_cache["commands_executed"]
                    ),
                    "kubently_commands_blocked_total": dict(
                        self._metrics_cache["commands_blocked"]
                    ),
                }

                # Add current stats
                stats = self.get_command_stats(hours=1)
                metrics["kubently_commands_last_hour"] = stats.get("total_commands", 0)
                metrics["kubently_commands_blocked_last_hour"] = stats.get("blocked", 0)

                return metrics

            except Exception as e:
                logger.error(f"Failed to export metrics: {e}")
                return {}

    def cleanup_old_data(self, days: int = 30) -> None:
        """
        Clean up old data from database.

        Args:
            days: Keep data for this many days
        """
        with self._lock:
            try:
                cursor = self._conn.cursor()

                cutoff = time.time() - (days * 86400)

                # Clean command history
                cursor.execute(
                    """
                    DELETE FROM command_history 
                    WHERE timestamp < ?
                """,
                    (cutoff,),
                )
                deleted_commands = cursor.rowcount

                # Clean config history
                cursor.execute(
                    """
                    DELETE FROM config_history 
                    WHERE timestamp < ?
                """,
                    (cutoff,),
                )
                deleted_configs = cursor.rowcount

                # Clean metrics
                cursor.execute(
                    """
                    DELETE FROM metrics 
                    WHERE timestamp < ?
                """,
                    (cutoff,),
                )
                deleted_metrics = cursor.rowcount

                # Vacuum to reclaim space
                cursor.execute("VACUUM")

                logger.info(
                    f"Cleaned up old data: {deleted_commands} commands, "
                    f"{deleted_configs} configs, {deleted_metrics} metrics"
                )

            except Exception as e:
                logger.error(f"Failed to cleanup old data: {e}")

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
