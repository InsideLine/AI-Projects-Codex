from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .chat_store import utc_now_iso


class DynamoDbChatStore:
    """DynamoDB-backed Teams chat/job store for deployed stateless runtimes."""

    def __init__(self, table_name: str, *, dynamodb_resource: Any | None = None) -> None:
        if not table_name:
            raise ValueError("table_name is required for DynamoDbChatStore.")
        if dynamodb_resource is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("boto3 is required for DynamoDbChatStore.") from exc
            dynamodb_resource = boto3.resource("dynamodb")
        self.table = dynamodb_resource.Table(table_name)

    def save_message(self, user_id: str, role: str, text: str) -> None:
        now = utc_now_iso()
        self.table.put_item(
            Item={
                "pk": _user_pk(user_id),
                "sk": f"MSG#{now}#{uuid.uuid4().hex[:8]}",
                "item_type": "message",
                "user_id": user_id,
                "role": role,
                "text": text,
                "created_at": now,
            }
        )

    def recent_messages(self, user_id: str, limit: int = 12) -> list[dict[str, Any]]:
        rows = self._query_user_prefix(user_id, "MSG#", limit)
        rows.reverse()
        return [
            {"role": row.get("role", ""), "text": row.get("text", ""), "created_at": row.get("created_at", "")}
            for row in rows
        ]

    def save_preference(
        self,
        user_id: str,
        preference_key: str,
        preference_value: str,
        *,
        confidence: float,
        source: str,
    ) -> None:
        now = utc_now_iso()
        self.table.put_item(
            Item={
                "pk": _user_pk(user_id),
                "sk": f"PREF#{now}#{uuid.uuid4().hex[:8]}",
                "item_type": "preference",
                "user_id": user_id,
                "preference_key": preference_key,
                "preference_value": preference_value,
                "confidence": str(float(confidence)),
                "source": source,
                "created_at": now,
            }
        )

    def get_preferences(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "preference_key": row.get("preference_key", ""),
                "preference_value": row.get("preference_value", ""),
                "confidence": float(row.get("confidence", 0.0)),
                "source": row.get("source", ""),
                "created_at": row.get("created_at", ""),
            }
            for row in self._query_user_prefix(user_id, "PREF#", limit)
        ]

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
        user_job_sk = f"JOB#{now}#{job_id}"
        job_item = {
            "pk": _job_pk(job_id),
            "sk": "META",
            "item_type": "job",
            "job_id": job_id,
            "user_id": user_id,
            "user_job_sk": user_job_sk,
            "job_type": job_type,
            "subject_type": subject_type,
            "subject_value": subject_value,
            "status": "queued",
            "payload_json": json.dumps(payload, sort_keys=True),
            "result_json": "null",
            "error_text": "",
            "created_at": now,
            "updated_at": now,
        }
        user_job_item = {**job_item, "pk": _user_pk(user_id), "sk": user_job_sk}
        self.table.put_item(Item=job_item)
        self.table.put_item(Item=user_job_item)
        return self.get_job(job_id) or {}

    def mark_processing(self, job_id: str) -> None:
        self._update_job(job_id, status="processing")

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        self._update_job(job_id, status="completed", result_json=json.dumps(result, sort_keys=True), error_text="")

    def mark_failed(self, job_id: str, error_text: str) -> None:
        self._update_job(job_id, status="failed", error_text=error_text)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        response = self.table.get_item(Key={"pk": _job_pk(job_id), "sk": "META"})
        item = response.get("Item")
        return self._decode_job(item) if item else None

    def recent_jobs(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return [self._decode_job(row) for row in self._query_user_prefix(user_id, "JOB#", limit)]

    def last_completed_job(self, user_id: str) -> dict[str, Any] | None:
        for job in self.recent_jobs(user_id, limit=25):
            if job.get("status") == "completed":
                return job
        return None

    def user_summary(self, user_id: str) -> dict[str, Any]:
        recent_jobs = self.recent_jobs(user_id, limit=25)
        counts: dict[tuple[str, str], int] = {}
        for job in recent_jobs:
            key = (str(job.get("subject_type", "")), str(job.get("subject_value", "")))
            counts[key] = counts.get(key, 0) + 1
        top_subjects = [
            {"subject_type": key[0], "subject_value": key[1], "request_count": count}
            for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0][1]))[:5]
        ]
        return {
            "total_jobs": len(recent_jobs),
            "top_subjects": top_subjects,
            "recent_jobs": recent_jobs[:5],
            "recent_messages": self.recent_messages(user_id, limit=8),
            "preferences": self.get_preferences(user_id, limit=8),
        }

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: str | None = None,
        error_text: str | None = None,
    ) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        now = utc_now_iso()
        item_updates = {
            "status": status,
            "updated_at": now,
        }
        if result_json is not None:
            item_updates["result_json"] = result_json
        if error_text is not None:
            item_updates["error_text"] = error_text
        for key in (
            {"pk": _job_pk(job_id), "sk": "META"},
            {"pk": _user_pk(str(job["user_id"])), "sk": str(job["user_job_sk"])},
        ):
            self._update_item(key, item_updates)

    def _update_item(self, key: dict[str, str], values: dict[str, str]) -> None:
        names: dict[str, str] = {}
        attr_values: dict[str, str] = {}
        expressions: list[str] = []
        for index, (field, value) in enumerate(values.items()):
            name_key = f"#n{index}"
            value_key = f":v{index}"
            names[name_key] = field
            attr_values[value_key] = value
            expressions.append(f"{name_key} = {value_key}")
        self.table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(expressions),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attr_values,
        )

    def _query_user_prefix(self, user_id: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(_user_pk(user_id)) & Key("sk").begins_with(prefix),
            ScanIndexForward=False,
            Limit=limit,
        )
        return list(response.get("Items") or [])

    def _decode_job(self, item: dict[str, Any]) -> dict[str, Any]:
        payload_json = str(item.get("payload_json") or "{}")
        result_json = str(item.get("result_json") or "null")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:
            result = None
        return {
            "job_id": str(item.get("job_id", "")),
            "user_id": str(item.get("user_id", "")),
            "user_job_sk": str(item.get("user_job_sk", "")),
            "job_type": str(item.get("job_type", "")),
            "subject_type": str(item.get("subject_type", "")),
            "subject_value": str(item.get("subject_value", "")),
            "status": str(item.get("status", "")),
            "payload": payload,
            "result": result,
            "error_text": str(item.get("error_text") or ""),
            "created_at": str(item.get("created_at", "")),
            "updated_at": str(item.get("updated_at", "")),
        }


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _job_pk(job_id: str) -> str:
    return f"JOB#{job_id}"
