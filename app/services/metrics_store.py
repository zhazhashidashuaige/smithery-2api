"""Metrics collection utilities with optional persistent storage backends."""
from __future__ import annotations

import logging
import sqlite3
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Deque, Dict, Iterable, List, Optional

from app.core.config import settings


logger = logging.getLogger(__name__)


def _summarize_records(records: Iterable[Dict]) -> Dict[str, float]:
    """Compute aggregate metrics for a collection of request records."""

    record_list = list(records)

    prompt_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in record_list)
    completion_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in record_list)
    total_tokens = prompt_tokens + completion_tokens

    request_count = len(record_list)
    success_count = sum(1 for item in record_list if item.get("status") == "success")
    error_count = request_count - success_count

    latencies = [
        float(duration)
        for duration in (item.get("duration_ms") for item in record_list)
        if isinstance(duration, (int, float))
    ]
    average_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "average_latency_ms": average_latency_ms,
    }


def _summarize_by_token(records: Iterable[Dict]) -> Dict[int, Dict[str, float]]:
    """Aggregate usage metrics grouped by token index."""

    summary: Dict[int, Dict[str, float]] = {}
    for item in records:
        token_index = item.get("token_index")
        try:
            key = int(token_index)
        except (TypeError, ValueError):
            continue

        token_data = summary.setdefault(
            key,
            {"usage_count": 0, "total_tokens": 0, "last_completed_at": None},
        )

        token_data["usage_count"] += 1
        token_data["total_tokens"] += int(item.get("total_tokens", 0) or 0)

        completed_at = item.get("completed_at")
        if isinstance(completed_at, (int, float)):
            last_completed = token_data["last_completed_at"]
            if last_completed is None or completed_at > last_completed:
                token_data["last_completed_at"] = float(completed_at)

    return summary


class InMemoryMetricsStore:
    """Thread-safe in-memory metrics storage using a ring buffer."""

    def __init__(self, max_records: int = 1000):
        self._records: Deque[Dict] = deque(maxlen=max_records)
        self._lock = Lock()

    def add_record(self, record: Dict) -> None:
        with self._lock:
            self._records.append(dict(record))

    def list_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> List[Dict]:
        with self._lock:
            snapshot = list(self._records)

        filtered: List[Dict] = []
        for item in snapshot:
            completed_at = item.get("completed_at")
            if start is not None or end is not None:
                if completed_at is None:
                    continue
                if start is not None and completed_at < start:
                    continue
                if end is not None and completed_at > end:
                    continue
            if model and item.get("model") != model:
                continue
            filtered.append(item)

        filtered.sort(key=lambda record: record.get("completed_at") or 0, reverse=newest_first)

        start_index = max(offset, 0)
        if start_index:
            filtered = filtered[start_index:]

        if limit is not None:
            filtered = filtered[:limit]

        return [dict(item) for item in filtered]

    def count_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> int:
        with self._lock:
            snapshot = list(self._records)

        count = 0
        for item in snapshot:
            completed_at = item.get("completed_at")
            if start is not None or end is not None:
                if completed_at is None:
                    continue
                if start is not None and completed_at < start:
                    continue
                if end is not None and completed_at > end:
                    continue
            if model and item.get("model") != model:
                continue
            count += 1

        return count

    def summarize_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[str, float]:
        return _summarize_records(
            self.list_records(start=start, end=end, model=model, newest_first=False)
        )

    def summarize_by_token(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[int, Dict[str, float]]:
        return _summarize_by_token(
            self.list_records(start=start, end=end, model=model, newest_first=False)
        )


class SQLiteMetricsStore:
    """SQLite-backed metrics storage for persistence across restarts."""

    def __init__(self, db_path: str):
        self._db_path = Path(db_path).expanduser()
        if not self._db_path.parent.exists():
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id TEXT PRIMARY KEY,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    started_at REAL,
                    completed_at REAL,
                    duration_ms REAL,
                    status TEXT,
                    error_message TEXT,
                    token_index INTEGER,
                    client_ip TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_metrics_completed_at ON request_metrics(completed_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_metrics_token_index ON request_metrics(token_index)"
            )
            columns = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(request_metrics)")
            }
            if "client_ip" not in columns:
                self._conn.execute("ALTER TABLE request_metrics ADD COLUMN client_ip TEXT")
            self._conn.commit()

    def add_record(self, record: Dict) -> None:
        record_id = record.get("id")
        if not record_id:
            logger.warning("Skipping metrics persistence for record without id: %s", record)
            return

        payload = {
            "id": str(record_id),
            "model": record.get("model"),
            "prompt_tokens": int(record.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(record.get("completion_tokens", 0) or 0),
            "total_tokens": int(record.get("total_tokens", 0) or 0),
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
            "duration_ms": record.get("duration_ms"),
            "status": record.get("status"),
            "error_message": record.get("error_message"),
            "token_index": record.get("token_index"),
            "client_ip": record.get("client_ip"),
        }

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO request_metrics (
                    id, model, prompt_tokens, completion_tokens, total_tokens,
                    started_at, completed_at, duration_ms, status, error_message, token_index, client_ip
                )
                VALUES (:id, :model, :prompt_tokens, :completion_tokens, :total_tokens,
                        :started_at, :completed_at, :duration_ms, :status, :error_message, :token_index, :client_ip)
                ON CONFLICT(id) DO UPDATE SET
                    model=excluded.model,
                    prompt_tokens=excluded.prompt_tokens,
                    completion_tokens=excluded.completion_tokens,
                    total_tokens=excluded.total_tokens,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    duration_ms=excluded.duration_ms,
                    status=excluded.status,
                    error_message=excluded.error_message,
                    token_index=excluded.token_index,
                    client_ip=excluded.client_ip
                """,
                payload,
            )
            self._conn.commit()

    def list_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> List[Dict]:
        order_clause = (
            "ORDER BY (completed_at IS NULL), completed_at DESC"
            if newest_first
            else "ORDER BY (completed_at IS NULL), completed_at ASC"
        )

        conditions = []
        params: List[object] = []

        if start is not None:
            conditions.append("completed_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("completed_at <= ?")
            params.append(end)
        if model:
            conditions.append("model = ?")
            params.append(model)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        pagination_clause = ""
        pagination_params: List[object] = []
        if limit is not None:
            pagination_clause = " LIMIT ?"
            pagination_params.append(int(limit))
            if offset:
                pagination_clause += " OFFSET ?"
                pagination_params.append(max(int(offset), 0))
        elif offset:
            pagination_clause = " LIMIT -1 OFFSET ?"
            pagination_params.append(max(int(offset), 0))

        params.extend(pagination_params)

        query = (
            "SELECT id, model, prompt_tokens, completion_tokens, total_tokens, "
            "started_at, completed_at, duration_ms, status, error_message, token_index, client_ip "
            "FROM request_metrics "
            f"{where_clause} {order_clause}{pagination_clause}"
        )

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        results: List[Dict] = []
        for row in rows:
            data = dict(row)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "token_index"):
                if data.get(key) is not None:
                    data[key] = int(data[key])
            if data.get("duration_ms") is not None:
                data["duration_ms"] = float(data["duration_ms"])
            results.append(data)

        return results

    def count_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> int:
        conditions = []
        params: List[object] = []

        if start is not None:
            conditions.append("completed_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("completed_at <= ?")
            params.append(end)
        if model:
            conditions.append("model = ?")
            params.append(model)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"SELECT COUNT(*) AS total FROM request_metrics{where_clause}"

        with self._lock:
            row = self._conn.execute(query, params).fetchone()

        if not row:
            return 0

        total = row["total"] if isinstance(row, sqlite3.Row) else row[0]
        return int(total or 0)

    def summarize_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[str, float]:
        conditions = []
        params: List[object] = []

        if start is not None:
            conditions.append("completed_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("completed_at <= ?")
            params.append(end)
        if model:
            conditions.append("model = ?")
            params.append(model)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = (
            "SELECT "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COUNT(*) AS request_count, "
            "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count, "
            "AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms END) AS average_latency_ms "
            "FROM request_metrics"
            f"{where_clause}"
        )

        with self._lock:
            row = self._conn.execute(query, params).fetchone()

        if row is None:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
                "success_count": 0,
                "error_count": 0,
                "average_latency_ms": 0.0,
            }

        request_count = int(row["request_count"] or 0)
        success_count = int(row["success_count"] or 0)
        error_count = request_count - success_count
        average_latency = float(row["average_latency_ms"]) if row["average_latency_ms"] is not None else 0.0

        return {
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "request_count": request_count,
            "success_count": success_count,
            "error_count": error_count,
            "average_latency_ms": average_latency,
        }

    def summarize_by_token(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[int, Dict[str, float]]:
        conditions = ["token_index IS NOT NULL"]
        params: List[object] = []

        if start is not None:
            conditions.append("completed_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("completed_at <= ?")
            params.append(end)
        if model:
            conditions.append("model = ?")
            params.append(model)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        query = (
            "SELECT token_index, "
            "COUNT(*) AS usage_count, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "MAX(completed_at) AS last_completed_at "
            "FROM request_metrics"
            f"{where_clause} "
            "GROUP BY token_index"
        )

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        summary: Dict[int, Dict[str, float]] = {}
        for row in rows:
            token_index = row["token_index"]
            if token_index is None:
                continue

            summary[int(token_index)] = {
                "usage_count": int(row["usage_count"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "last_completed_at": float(row["last_completed_at"]) if row["last_completed_at"] is not None else None,
            }

        return summary


class MetricsStore:
    """Facade exposing a unified API for metrics storage backends."""

    def __init__(self, backend):
        self._backend = backend

    def add_record(self, record: Dict) -> None:
        self._backend.add_record(record)

    def list_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> List[Dict]:
        return self._backend.list_records(
            start=start,
            end=end,
            model=model,
            limit=limit,
            offset=offset,
            newest_first=newest_first,
        )

    def count_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> int:
        return self._backend.count_records(start=start, end=end, model=model)

    def summarize_records(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[str, float]:
        return self._backend.summarize_records(start=start, end=end, model=model)

    def summarize_by_token(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[int, Dict[str, float]]:
        return self._backend.summarize_by_token(start=start, end=end, model=model)

    def aggregate(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Dict[str, float]:
        return self.summarize_records(start=start, end=end, model=model)


def _initialize_metrics_store() -> MetricsStore:
    if settings.METRICS_DB_PATH:
        logger.info("Using SQLite metrics store at %s", settings.METRICS_DB_PATH)
        backend = SQLiteMetricsStore(settings.METRICS_DB_PATH)
    else:
        logger.info(
            "Using in-memory metrics store with max %s records", settings.METRICS_MAX_IN_MEMORY_RECORDS
        )
        backend = InMemoryMetricsStore(max_records=settings.METRICS_MAX_IN_MEMORY_RECORDS)

    return MetricsStore(backend)


metrics_store = _initialize_metrics_store()

