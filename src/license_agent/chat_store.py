from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


class ChatStore:
    """Local SQLite store for Teams-style chat state, queued jobs, and lightweight memory."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_value TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def save_message(self, user_id: str, role: str, text: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages(user_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (user_id, role, text, utc_now_iso()),
            )

    def recent_messages(self, user_id: str, limit: int = 12) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            """
            SELECT role, text, created_at
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows.reverse()
        return rows

    def save_preference(
        self,
        user_id: str,
        preference_key: str,
        preference_value: str,
        *,
        confidence: float,
        source: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO preferences(user_id, preference_key, preference_value, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, preference_key, preference_value, float(confidence), source, utc_now_iso()),
            )

    def get_preferences(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            """
            SELECT preference_key, preference_value, confidence, source, created_at
            FROM preferences
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def create_job(
        self,
        *,
        user_id: str,
        job_type: str,
        subject_type: str,
        subject_value: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs(
                    job_id, user_id, job_type, subject_type, subject_value, status,
                    payload_json, result_json, error_text, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, '', ?, ?)
                """,
                (job_id, user_id, job_type, subject_type, subject_value, json.dumps(payload, sort_keys=True), now, now),
            )
        return self.get_job(job_id) or {}

    def mark_processing(self, job_id: str) -> None:
        self._update_job(job_id, status="processing")

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        self._update_job(job_id, status="completed", result_json=json.dumps(result, sort_keys=True), error_text="")

    def mark_failed(self, job_id: str, error_text: str) -> None:
        self._update_job(job_id, status="failed", error_text=error_text)

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: str | None = None,
        error_text: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, utc_now_iso()]
        if result_json is not None:
            fields.append("result_json = ?")
            values.append(result_json)
        if error_text is not None:
            fields.append("error_text = ?")
            values.append(error_text)
        values.append(job_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", values)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            """
            SELECT job_id, user_id, job_type, subject_type, subject_value, status,
                   payload_json, result_json, error_text, created_at, updated_at
            FROM jobs
            WHERE job_id = ?
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        return self._decode_job(row) if row else None

    def recent_jobs(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            """
            SELECT job_id, user_id, job_type, subject_type, subject_value, status,
                   payload_json, result_json, error_text, created_at, updated_at
            FROM jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._decode_job(row) for row in cursor.fetchall()]

    def last_completed_job(self, user_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            """
            SELECT job_id, user_id, job_type, subject_type, subject_value, status,
                   payload_json, result_json, error_text, created_at, updated_at
            FROM jobs
            WHERE user_id = ? AND status = 'completed'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return self._decode_job(row) if row else None

    def user_summary(self, user_id: str) -> dict[str, Any]:
        top_subjects_cursor = self._conn.execute(
            """
            SELECT subject_type, subject_value, COUNT(*) AS request_count
            FROM jobs
            WHERE user_id = ?
            GROUP BY subject_type, subject_value
            ORDER BY request_count DESC, subject_value ASC
            LIMIT 5
            """,
            (user_id,),
        )
        total_jobs_cursor = self._conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ?", (user_id,))
        total_jobs = int(total_jobs_cursor.fetchone()[0])
        return {
            "total_jobs": total_jobs,
            "top_subjects": [dict(row) for row in top_subjects_cursor.fetchall()],
            "recent_jobs": self.recent_jobs(user_id, limit=5),
            "recent_messages": self.recent_messages(user_id, limit=8),
            "preferences": self.get_preferences(user_id, limit=8),
        }

    def _decode_job(self, row: sqlite3.Row) -> dict[str, Any]:
        payload_json = str(row["payload_json"] or "{}")
        result_json = str(row["result_json"] or "null")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            result = None
        return {
            "job_id": str(row["job_id"]),
            "user_id": str(row["user_id"]),
            "job_type": str(row["job_type"]),
            "subject_type": str(row["subject_type"]),
            "subject_value": str(row["subject_value"]),
            "status": str(row["status"]),
            "payload": payload,
            "result": result,
            "error_text": str(row["error_text"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
