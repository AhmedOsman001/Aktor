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
    run_count INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0
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
    smart_wait_enabled INTEGER DEFAULT 0,
    smart_wait_timeout REAL DEFAULT 10.0,
    smart_wait_on_timeout TEXT DEFAULT 'stop',
    automation_id TEXT,
    class_name TEXT,
    parent_path TEXT,
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
        _migrate_smart_wait(conn)
        _migrate_element_attrs(conn)
        _migrate_favorite(conn)
        logger.debug("init_db: tables ready at %s", db_path or DB_PATH)
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


def _migrate_smart_wait(conn: sqlite3.Connection) -> None:
    """Add Smart Wait columns to existing databases without data loss."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(steps)").fetchall()]
        if "smart_wait_enabled" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN smart_wait_enabled INTEGER DEFAULT 0")
        if "smart_wait_timeout" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN smart_wait_timeout REAL DEFAULT 10.0")
        if "smart_wait_on_timeout" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN smart_wait_on_timeout TEXT DEFAULT 'stop'")
        conn.commit()
    except Exception:
        pass


def _migrate_favorite(conn: sqlite3.Connection) -> None:
    """Add the favorite flag to existing workflow tables without data loss."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(workflows)").fetchall()]
        if "favorite" not in cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    except Exception:
        pass


def _migrate_element_attrs(conn: sqlite3.Connection) -> None:
    """Add UI element attribute columns (automation_id / class_name / parent_path)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(steps)").fetchall()]
        added = False
        if "automation_id" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN automation_id TEXT")
            added = True
        if "class_name" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN class_name TEXT")
            added = True
        if "parent_path" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN parent_path TEXT")
            added = True
        if added:
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
            _insert_step(conn, workflow_id, order, step)

        conn.commit()
        logger.debug("save_workflow: id=%d name=%r hotkey=%r steps=%d", workflow_id, workflow.name, workflow.trigger.hotkey, len(workflow.steps))
        return workflow_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_steps(workflow_id: int, steps: list[ActionStep], db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM steps WHERE workflow_id = ?", (workflow_id,))
        for order, step in enumerate(steps):
            _insert_step(conn, workflow_id, order, step)
        conn.commit()
        logger.debug("save_steps: workflow_id=%d replaced with %d steps", workflow_id, len(steps))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_step(conn: sqlite3.Connection, workflow_id: int, order: int, step: ActionStep) -> None:
    conn.execute(
        "INSERT INTO steps (workflow_id, step_order, type, app_name, window_title, "
        "element_name, element_type, automation_id, class_name, parent_path, "
        "x, y, x_relative, y_relative, keys, text, "
        "scroll_dx, scroll_dy, delay_after, description, enabled, "
        "smart_wait_enabled, smart_wait_timeout, smart_wait_on_timeout) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            workflow_id,
            order,
            step.type,
            step.app_name,
            step.window_title,
            step.element_name,
            step.element_type,
            step.automation_id,
            step.class_name,
            step.parent_path,
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
            1 if step.smart_wait_enabled else 0,
            step.smart_wait_timeout,
            step.smart_wait_on_timeout,
        ),
    )


def get_workflow(workflow_id: int, db_path: Optional[Path] = None) -> Optional[Workflow]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            logger.debug("get_workflow: id=%d not found", workflow_id)
            return None

        workflow = _row_to_workflow(row)

        step_rows = conn.execute(
            "SELECT * FROM steps WHERE workflow_id = ? ORDER BY step_order",
            (workflow_id,),
        ).fetchall()
        workflow.steps = [_row_to_step(r) for r in step_rows]
        logger.debug("get_workflow: id=%d name=%r steps=%d", workflow_id, workflow.name, len(workflow.steps))
        return workflow
    finally:
        conn.close()


def get_all_workflows(db_path: Optional[Path] = None) -> list[Workflow]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM workflows ORDER BY (last_run IS NULL), last_run DESC, created_at DESC"
        ).fetchall()

        workflows = []
        for row in rows:
            wf = _row_to_workflow(row)
            agg = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(delay_after), 0) AS d "
                "FROM steps WHERE workflow_id = ?", (wf.id,)
            ).fetchone()
            wf._step_count = int(agg["c"])
            wf._duration_secs = float(agg["d"])
            wf.steps = []
            workflows.append(wf)

        logger.debug("get_all_workflows: %d workflows", len(workflows))
        return workflows
    finally:
        conn.close()


def delete_workflow(workflow_id: int, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM steps WHERE workflow_id = ?", (workflow_id,))
        conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.commit()
        logger.debug("delete_workflow: id=%d", workflow_id)
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
        logger.debug("update_workflow_name: id=%d name=%r", workflow_id, name)
    finally:
        conn.close()


def set_favorite(workflow_id: int, favorite: bool, db_path: Optional[Path] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE workflows SET favorite = ? WHERE id = ?",
            (1 if favorite else 0, workflow_id),
        )
        conn.commit()
        logger.debug("set_favorite: id=%d favorite=%s", workflow_id, favorite)
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
        logger.debug("update_last_run: id=%d at=%s", workflow_id, now)
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
        logger.debug("update_trigger: id=%d hotkey=%r voice=%r", workflow_id, hotkey, voice_phrase)
    finally:
        conn.close()


def _row_to_workflow(row: sqlite3.Row) -> Workflow:
    favorite = bool(row["favorite"]) if "favorite" in row.keys() else False
    return Workflow(
        id=row["id"],
        name=row["name"],
        trigger=Trigger(
            hotkey=row["trigger_hotkey"],
            voice_phrase=row["trigger_voice"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
        favorite=favorite,
    )


def _row_to_step(row: sqlite3.Row) -> ActionStep:
    keys = row.keys()
    enabled_val = row["enabled"] if "enabled" in keys else 1
    sw_enabled = row["smart_wait_enabled"] if "smart_wait_enabled" in keys else 0
    sw_timeout = row["smart_wait_timeout"] if "smart_wait_timeout" in keys else None
    sw_on_timeout = row["smart_wait_on_timeout"] if "smart_wait_on_timeout" in keys else None
    automation_id = row["automation_id"] if "automation_id" in keys else None
    class_name = row["class_name"] if "class_name" in keys else None
    parent_path = row["parent_path"] if "parent_path" in keys else None
    return ActionStep(
        id=row["id"],
        type=row["type"],
        app_name=row["app_name"],
        window_title=row["window_title"],
        element_name=row["element_name"],
        element_type=row["element_type"],
        automation_id=automation_id,
        class_name=class_name,
        parent_path=parent_path,
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
        smart_wait_enabled=bool(sw_enabled),
        smart_wait_timeout=sw_timeout if sw_timeout is not None else 10.0,
        smart_wait_on_timeout=sw_on_timeout or "stop",
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
