import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from flowrecord.config import DB_PATH
from flowrecord.models import ActionStep, Trigger, Workflow

logger = logging.getLogger(__name__)

_CREATE_WORKFLOWS_TABLE = """
CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trigger_hotkey TEXT,
    trigger_voice TEXT,
    created_at TEXT NOT NULL,
    last_run TEXT,
    run_count INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_STEPS_TABLE = """
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    step_order INTEGER NOT NULL,
    type TEXT NOT NULL,
    app_name TEXT,
    window_title TEXT,
    element_name TEXT,
    element_type TEXT,
    x INTEGER,
    y INTEGER,
    x_relative REAL,
    y_relative REAL,
    keys TEXT,
    text TEXT,
    scroll_dx INTEGER DEFAULT 0,
    scroll_dy INTEGER DEFAULT 0,
    delay_after REAL DEFAULT 0.0,
    description TEXT,
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
)
"""


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(_CREATE_WORKFLOWS_TABLE)
        conn.execute(_CREATE_STEPS_TABLE)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(steps)").fetchall()]
        if "enabled" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN enabled INTEGER DEFAULT 1")
            conn.commit()
    except Exception:
        pass


def save_workflow(workflow: Workflow, db_path: Optional[Path] = None) -> int:
    conn = _get_conn(db_path)
    try:
        now = datetime.now().isoformat()
        cursor = conn.execute(
            "INSERT INTO workflows (name, trigger_hotkey, trigger_voice, created_at, last_run, run_count) "
            "VALUES (?, ?, ?, ?, NULL, 0)",
            (
                workflow.name,
                workflow.trigger.hotkey,
                workflow.trigger.voice_phrase,
                now,
            ),
        )
        workflow_id = cursor.lastrowid

        for order, step in enumerate(workflow.steps):
            conn.execute(
                "INSERT INTO steps (workflow_id, step_order, type, app_name, window_title, "
                "element_name, element_type, x, y, x_relative, y_relative, keys, text, "
                "scroll_dx, scroll_dy, delay_after, description, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workflow_id,
                    order,
                    step.type,
                    step.app_name,
                    step.window_title,
                    step.element_name,
                    step.element_type,
                    step.x,
                    step.y,
                    step.x_relative,
                    step.y_relative,
                    step.keys,
                    step.text,
                    step.scroll_dx,
                    step.scroll_dy,
                    step.delay_after,
                    step.description,
                    1 if step.enabled else 0,
                ),
            )

        conn.commit()
        return workflow_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_workflow(workflow_id: int, db_path: Optional[Path] = None) -> Optional[Workflow]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            return None

        workflow = _row_to_workflow(row)

        step_rows = conn.execute(
            "SELECT * FROM steps WHERE workflow_id = ? ORDER BY step_order",
            (workflow_id,),
        ).fetchall()
        workflow.steps = [_row_to_step(r) for r in step_rows]
        return workflow
    finally:
        conn.close()


def get_all_workflows(db_path: Optional[Path] = None) -> list[Workflow]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM workflows ORDER BY created_at DESC"
        ).fetchall()

        workflows = []
        for row in rows:
            wf = _row_to_workflow(row)
            step_count = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE workflow_id = ?", (wf.id,)
            ).fetchone()[0]
            wf.steps = []
            workflows.append(wf)

        return workflows
    finally:
        conn.close()


def delete_workflow(workflow_id: int, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM steps WHERE workflow_id = ?", (workflow_id,))
        conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_workflow_name(workflow_id: int, name: str, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE workflows SET name = ? WHERE id = ?", (name, workflow_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_last_run(workflow_id: int, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE workflows SET last_run = ?, run_count = run_count + 1 WHERE id = ?",
            (now, workflow_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_trigger(workflow_id: int, hotkey: Optional[str] = None, voice_phrase: Optional[str] = None, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        if hotkey is not None:
            conn.execute(
                "UPDATE workflows SET trigger_hotkey = ? WHERE id = ?",
                (hotkey, workflow_id),
            )
        if voice_phrase is not None:
            conn.execute(
                "UPDATE workflows SET trigger_voice = ? WHERE id = ?",
                (voice_phrase, workflow_id),
            )
        conn.commit()
    finally:
        conn.close()


def _row_to_workflow(row: sqlite3.Row) -> Workflow:
    return Workflow(
        id=row["id"],
        name=row["name"],
        trigger=Trigger(
            hotkey=row["trigger_hotkey"],
            voice_phrase=row["trigger_voice"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
    )


def _row_to_step(row: sqlite3.Row) -> ActionStep:
    enabled_val = row["enabled"] if "enabled" in row.keys() else 1
    return ActionStep(
        id=row["id"],
        type=row["type"],
        app_name=row["app_name"],
        window_title=row["window_title"],
        element_name=row["element_name"],
        element_type=row["element_type"],
        x=row["x"],
        y=row["y"],
        x_relative=row["x_relative"],
        y_relative=row["y_relative"],
        keys=row["keys"],
        text=row["text"],
        scroll_dx=row["scroll_dx"] or 0,
        scroll_dy=row["scroll_dy"] or 0,
        delay_after=row["delay_after"] or 0.0,
        description=row["description"],
        enabled=bool(enabled_val),
    )


def update_step_enabled(step_id: int, enabled: bool, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE steps SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, step_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_step_delay(step_id: int, delay: float, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE steps SET delay_after = ? WHERE id = ?",
            (delay, step_id),
        )
        conn.commit()
    finally:
        conn.close()
