"""SQLite database layer for the product catalog."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                product_id   TEXT PRIMARY KEY,
                name         TEXT NOT NULL DEFAULT '',
                image_path   TEXT NOT NULL DEFAULT '',
                fact_card_path TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_codes (
                code         TEXT NOT NULL UNIQUE,
                product_id   TEXT NOT NULL REFERENCES products(product_id),
                created_at   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_product_codes_product
                ON product_codes(product_id);

            CREATE TABLE IF NOT EXISTS dispatch_tasks (
                task_id        TEXT PRIMARY KEY,
                wx_remark      TEXT NOT NULL,
                return_code    TEXT NOT NULL DEFAULT '',
                send_codes     TEXT NOT NULL,
                countdown_days INTEGER NOT NULL DEFAULT 3,
                created_at     TEXT NOT NULL,
                trigger_at     TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                fail_reason    TEXT,
                generation_started_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_dispatch_tasks_status_trigger
                ON dispatch_tasks(status, trigger_at);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(dispatch_tasks)")}
        if "fail_reason" not in columns:
            conn.execute("ALTER TABLE dispatch_tasks ADD COLUMN fail_reason TEXT")
        if "generation_started_at" not in columns:
            conn.execute("ALTER TABLE dispatch_tasks ADD COLUMN generation_started_at TEXT")

        # Products dimension columns (幂等)
        prod_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        for col, typ in [
            ("height_cm", "REAL"),
            ("width_cm", "REAL"),
            ("depth_cm", "REAL"),
            ("weight_kg", "REAL"),
            ("size_source", "TEXT"),
            ("room", "TEXT"),
        ]:
            if col not in prod_columns:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")

        conn.commit()
    finally:
        conn.close()


# --------------- Validation ---------------

class CodeConflictError(Exception):
    def __init__(self, code: str, existing_product_id: str):
        self.code = code
        self.existing_product_id = existing_product_id
        super().__init__(f"编号 '{code}' 已被产品 {existing_product_id} 占用")


class CodeEmptyError(Exception):
    pass


def validate_code_unique(db_path: Path, code: str) -> None:
    if not code or not code.strip():
        raise CodeEmptyError()
    code = code.strip()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT product_id FROM product_codes WHERE code = ?", (code,)
        ).fetchone()
        if row:
            raise CodeConflictError(code, row["product_id"])
    finally:
        conn.close()


# --------------- Products CRUD ---------------

def upsert_product(
    db_path: Path,
    product_id: str,
    name: str,
    image_path: str,
    fact_card_path: str,
    created_at: str,
    *,
    height_cm: float | None = None,
    width_cm: float | None = None,
    depth_cm: float | None = None,
    weight_kg: float | None = None,
    size_source: str | None = None,
    room: str | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO products (product_id, name, image_path, fact_card_path, created_at,
                                    height_cm, width_cm, depth_cm, weight_kg, size_source, room)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET
                   name = excluded.name,
                   image_path = excluded.image_path,
                   fact_card_path = excluded.fact_card_path,
                   height_cm = excluded.height_cm,
                   width_cm = excluded.width_cm,
                   depth_cm = excluded.depth_cm,
                   weight_kg = excluded.weight_kg,
                   size_source = excluded.size_source,
                   room = excluded.room
            """,
            (product_id, name, image_path, fact_card_path, created_at,
             height_cm, width_cm, depth_cm, weight_kg, size_source, room),
        )
        conn.commit()
    finally:
        conn.close()


def list_products(db_path: Path) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT product_id, name, image_path, fact_card_path, created_at,
                      height_cm, width_cm, depth_cm, weight_kg, size_source, room
               FROM products ORDER BY created_at DESC"""
        ).fetchall()
        products = []
        for r in rows:
            codes = conn.execute(
                "SELECT code, created_at FROM product_codes WHERE product_id = ? ORDER BY created_at",
                (r["product_id"],),
            ).fetchall()
            products.append({
                "product_id": r["product_id"],
                "name": r["name"],
                "image_path": r["image_path"],
                "fact_card_path": r["fact_card_path"],
                "created_at": r["created_at"],
                "height_cm": r["height_cm"],
                "width_cm": r["width_cm"],
                "depth_cm": r["depth_cm"],
                "weight_kg": r["weight_kg"],
                "size_source": r["size_source"],
                "room": r["room"],
                "codes": [{"code": c["code"], "created_at": c["created_at"]} for c in codes],
            })
        return products
    finally:
        conn.close()


# --------------- Codes CRUD ---------------

def add_code(db_path: Path, product_id: str, code: str) -> dict[str, str]:
    code = code.strip()
    validate_code_unique(db_path, code)
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO product_codes (code, product_id, created_at) VALUES (?, ?, ?)",
            (code, product_id, now),
        )
        conn.commit()
        return {"code": code, "product_id": product_id, "created_at": now}
    finally:
        conn.close()


def list_codes(db_path: Path, product_id: str) -> list[dict[str, str]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT code, created_at FROM product_codes WHERE product_id = ? ORDER BY created_at",
            (product_id,),
        ).fetchall()
        return [{"code": r["code"], "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()


def product_exists(db_path: Path, product_id: str) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_product(db_path: Path, product_id: str) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def delete_product(db_path: Path, product_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM product_codes WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
        conn.commit()
    finally:
        conn.close()


# --------------- Lookup by code ---------------

def lookup_product_by_code(db_path: Path, code: str) -> dict[str, str] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT p.product_id, p.name, p.image_path, p.fact_card_path,
                      p.height_cm, p.width_cm, p.depth_cm, p.weight_kg, p.size_source, p.room
               FROM product_codes pc
               JOIN products p ON p.product_id = pc.product_id
               WHERE pc.code = ?""",
            (code.strip(),),
        ).fetchone()
        if not row:
            return None
        return {
            "product_id": row["product_id"],
            "name": row["name"],
            "image_path": row["image_path"],
            "fact_card_path": row["fact_card_path"],
            "height_cm": row["height_cm"],
            "width_cm": row["width_cm"],
            "depth_cm": row["depth_cm"],
            "weight_kg": row["weight_kg"],
            "size_source": row["size_source"],
            "room": row["room"],
        }
    finally:
        conn.close()


# --------------- Dispatch Tasks ---------------

def create_dispatch_task(
    db_path: Path,
    task_id: str,
    wx_remark: str,
    return_code: str,
    send_codes: list[str],
    countdown_days: int,
    created_at: str,
    trigger_at: str,
) -> dict[str, Any]:
    import json as _json
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO dispatch_tasks
               (task_id, wx_remark, return_code, send_codes, countdown_days, created_at, trigger_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (task_id, wx_remark, return_code, _json.dumps(send_codes, ensure_ascii=False),
             countdown_days, created_at, trigger_at),
        )
        conn.commit()
        return {
            "task_id": task_id,
            "wx_remark": wx_remark,
            "return_code": return_code,
            "send_codes": send_codes,
            "countdown_days": countdown_days,
            "created_at": created_at,
            "trigger_at": trigger_at,
            "status": "pending",
            "fail_reason": None,
            "generation_started_at": None,
        }
    finally:
        conn.close()


def list_dispatch_tasks(db_path: Path) -> list[dict[str, Any]]:
    import json as _json
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM dispatch_tasks ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "task_id": r["task_id"],
                "wx_remark": r["wx_remark"],
                "return_code": r["return_code"],
                "send_codes": _json.loads(r["send_codes"]),
                "countdown_days": r["countdown_days"],
                "created_at": r["created_at"],
                "trigger_at": r["trigger_at"],
                "status": r["status"],
                "fail_reason": r["fail_reason"],
                "generation_started_at": r["generation_started_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_dispatch_task(db_path: Path, task_id: str) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM dispatch_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "wx_remark": row["wx_remark"],
            "return_code": row["return_code"],
            "send_codes": json.loads(row["send_codes"]),
            "countdown_days": row["countdown_days"],
            "created_at": row["created_at"],
            "trigger_at": row["trigger_at"],
            "status": row["status"],
            "fail_reason": row["fail_reason"],
            "generation_started_at": row["generation_started_at"],
        }
    finally:
        conn.close()


def recover_generating_dispatch_tasks(db_path: Path) -> int:
    """A single-process restart makes every in-progress task an orphan."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'pending', generation_started_at = NULL
               WHERE status = 'generating'"""
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("trigger_at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def list_due_pending_dispatch_tasks(db_path: Path, now: datetime) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM dispatch_tasks WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "wx_remark": row["wx_remark"],
                "return_code": row["return_code"],
                "send_codes": json.loads(row["send_codes"]),
                "countdown_days": row["countdown_days"],
                "created_at": row["created_at"],
                "trigger_at": row["trigger_at"],
                "status": row["status"],
                "fail_reason": row["fail_reason"],
                "generation_started_at": row["generation_started_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def claim_dispatch_task(db_path: Path, task_id: str, started_at: str) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'generating', generation_started_at = ?, fail_reason = NULL
               WHERE task_id = ? AND status = 'pending'""",
            (started_at, task_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_dispatch_task_ready(db_path: Path, task_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'ready', fail_reason = NULL
               WHERE task_id = ? AND status = 'generating'""",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_dispatch_task_awaiting_confirmation(
    db_path: Path, task_id: str, fail_reason: str | None = None
) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'awaiting_confirmation', fail_reason = ?
               WHERE task_id = ? AND status = 'sending'""",
            (fail_reason, task_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_dispatch_task_needs_review(db_path: Path, task_id: str, fail_reason: str) -> bool:
    """Mark a task as needs_review (from generating or sending)."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'needs_review', fail_reason = ?
               WHERE task_id = ? AND status IN ('generating', 'sending')""",
            (fail_reason, task_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def retry_dispatch_task_after_review(db_path: Path, task_id: str) -> bool:
    """Release a UIA-verification failure only after an explicit human review."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'ready', fail_reason = NULL
               WHERE task_id = ? AND status = 'needs_review'""",
            (task_id,),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def confirm_dispatch_task_sent(db_path: Path, task_id: str) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'sent'
               WHERE task_id = ? AND status = 'awaiting_confirmation'""",
            (task_id,),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_dispatch_task_sent(db_path: Path, task_id: str) -> bool:
    """Backward-compatible name for confirming an acknowledged send."""
    return confirm_dispatch_task_sent(db_path, task_id)




def recover_sending_dispatch_tasks(db_path: Path) -> int:
    dispatch_root = (db_path.parent / "dispatch").resolve()

    def has_submission_uncertainty(task_id: str) -> bool:
        task_dir = (dispatch_root / task_id).resolve()
        if task_dir.parent != dispatch_root:
            return False
        try:
            manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        results = manifest.get("results") if isinstance(manifest, dict) else None
        return isinstance(results, list) and any(
            isinstance(result, dict) and result.get("status") == "submission_uncertain"
            for result in results
        )

    conn = _connect(db_path)
    try:
        task_ids = [
            row["task_id"]
            for row in conn.execute(
                "SELECT task_id FROM dispatch_tasks WHERE status = 'sending'"
            ).fetchall()
        ]
        recovered = 0
        for task_id in task_ids:
            if has_submission_uncertainty(task_id):
                cursor = conn.execute(
                    """UPDATE dispatch_tasks
                       SET status = 'awaiting_confirmation', fail_reason = 'SEND_ACKNOWLEDGMENT_UNCERTAIN'
                       WHERE task_id = ? AND status = 'sending'""",
                    (task_id,),
                )
            else:
                cursor = conn.execute(
                    """UPDATE dispatch_tasks
                       SET status = 'ready', fail_reason = 'SEND_INTERRUPTED'
                       WHERE task_id = ? AND status = 'sending'""",
                    (task_id,),
                )
            recovered += cursor.rowcount
        conn.commit()
        return recovered
    finally:
        conn.close()


def mark_dispatch_task_send_failed(db_path: Path, task_id: str, fail_reason: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE dispatch_tasks SET status = 'ready', fail_reason = ? WHERE task_id = ? AND status = 'sending'",
            (fail_reason, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_ready_dispatch_tasks(db_path: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    import json as _json
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM dispatch_tasks WHERE status = 'ready' ORDER BY created_at"
        ).fetchall()
        due = []
        for r in rows:
            try:
                is_due = _parse_utc(r["trigger_at"]) <= current
            except ValueError:
                continue
            if is_due:
                due.append({
                    "task_id": r["task_id"],
                    "wx_remark": r["wx_remark"],
                    "return_code": r["return_code"],
                    "send_codes": _json.loads(r["send_codes"]),
                    "countdown_days": r["countdown_days"],
                    "created_at": r["created_at"],
                    "trigger_at": r["trigger_at"],
                    "status": r["status"],
                    "fail_reason": r["fail_reason"],
                    "generation_started_at": r["generation_started_at"],
                })
        return due
    finally:
        conn.close()


def claim_dispatch_task_sending(db_path: Path, task_id: str) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE dispatch_tasks SET status = 'sending', fail_reason = NULL WHERE task_id = ? AND status = 'ready'",
            (task_id,),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_dispatch_task_failed(db_path: Path, task_id: str, fail_reason: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """UPDATE dispatch_tasks
               SET status = 'failed', fail_reason = ?
               WHERE task_id = ? AND status = 'generating'""",
            (fail_reason, task_id),
        )
        conn.commit()
    finally:
        conn.close()
