"""Local pairing and durable protection against duplicate sends."""

import os
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

BEIJING = timezone(timedelta(hours=8))
DAILY_LIMIT = 150


def beijing_today() -> date:
    return datetime.now(BEIJING).date()


class BossError(Exception):
    def __init__(self, code: str, message: str, next_action: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


def default_data_dir() -> Path:
    override = os.environ.get("BOSS_ZP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return root / "boss-zhipin-mcp"


def bridge_pairing_token(directory: Path) -> str:
    """Keep the local extension pairing separate from browser credentials."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'extension-pairing-token'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text(encoding='ascii').strip()
    else:
        token = secrets.token_urlsafe(32)
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            handle.write(token)
            handle.flush()
    if len(token) < 40 or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for ch in token):
        raise BossError('PAIRING_INVALID', '本地配对密钥不可用。')
    return token


class GreetingLedger:
    """Reserve before the click. Crashes/timeouts leave an unknown result, never a retry."""

    def __init__(self, directory: Path):
        self.path = directory / "greetings.sqlite3"
        self._schema_ready = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            if not self._schema_ready or not existed:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS greetings ("
                        "job_id TEXT PRIMARY KEY, outcome TEXT NOT NULL, updated_at TEXT NOT NULL, budget_day TEXT)"
                    )
                    if "budget_day" not in {row[1] for row in conn.execute("PRAGMA table_info(greetings)")}:
                        conn.execute("ALTER TABLE greetings ADD COLUMN budget_day TEXT")
                        conn.execute("UPDATE greetings SET budget_day=date(updated_at, '+8 hours') "
                                     "WHERE outcome IN ('sent', 'unknown')")
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS not_clicked_attempts ("
                        "job_id TEXT NOT NULL, recorded_at TEXT NOT NULL, reason TEXT NOT NULL)"
                    )
                self._schema_ready = True
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, job_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute("SELECT outcome FROM greetings WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def get_many(self, job_ids: list[str]) -> dict[str, str]:
        if not job_ids:
            return {}
        with self._connection() as conn:
            return self._outcomes(conn, job_ids)

    @staticmethod
    def _outcomes(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, str]:
        placeholders = ",".join("?" for _ in job_ids)
        return dict(conn.execute(f"SELECT job_id, outcome FROM greetings WHERE job_id IN ({placeholders})", job_ids))

    @staticmethod
    def _used(conn: sqlite3.Connection, day: date) -> int:
        return conn.execute(
            "SELECT count(*) FROM greetings WHERE budget_day=? AND outcome IN ('sent', 'unknown')",
            (day.isoformat(),),
        ).fetchone()[0]

    def _quota(self, conn: sqlite3.Connection, day: date) -> dict:
        used = self._used(conn, day)
        return {"day": day.isoformat(), "limit": DAILY_LIMIT, "used": used, "remaining": max(0, DAILY_LIMIT - used)}

    def quota(self, day: date) -> dict:
        with self._connection() as conn:
            return self._quota(conn, day)

    def review_snapshot(self, job_ids: list[str], day: date) -> tuple[dict[str, str], dict]:
        with self._connection() as conn:
            conn.execute("BEGIN")
            return self._outcomes(conn, job_ids), self._quota(conn, day)

    def reserve(self, job_id: str, batch_day: date | None = None) -> bool:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            day = beijing_today()
            if batch_day is not None and day != batch_day:
                raise BossError('DAY_CHANGED', '已跨北京时间零点，请重新准备审核清单。')
            if self._used(conn, day) >= DAILY_LIMIT:
                raise BossError('LOCAL_DAILY_LIMIT', '今日 MCP 发送额度已用完。')
            # Archive legacy not_clicked rows before changing their state.
            conn.execute(
                "INSERT INTO not_clicked_attempts SELECT job_id, updated_at, 'legacy_not_clicked' "
                "FROM greetings WHERE job_id=? AND outcome='not_clicked' AND NOT EXISTS "
                "(SELECT 1 FROM not_clicked_attempts WHERE job_id=greetings.job_id AND recorded_at=greetings.updated_at)",
                (job_id,),
            )
            cursor = conn.execute(
                "INSERT INTO greetings (job_id, outcome, updated_at, budget_day) VALUES (?, 'unknown', ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET outcome='unknown', updated_at=excluded.updated_at, "
                "budget_day=excluded.budget_day WHERE greetings.outcome IN ('not_clicked', 'rejected')",
                (job_id, datetime.now(timezone.utc).isoformat(), day.isoformat()),
            )
        return cursor.rowcount == 1

    def mark_sent(self, job_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE greetings SET outcome='sent', updated_at=? WHERE job_id=? AND outcome='unknown'",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )

    def mark_not_clicked(self, job_id: str, reason: str = "precheck_failed") -> None:
        with self._connection() as conn:
            stamp = datetime.now(timezone.utc).isoformat()
            changed = conn.execute(
                "UPDATE greetings SET outcome='not_clicked', updated_at=? WHERE job_id=? AND outcome='unknown'",
                (stamp, job_id),
            ).rowcount
            if changed:
                conn.execute("INSERT INTO not_clicked_attempts VALUES (?, ?, ?)", (job_id, stamp, reason))

    def mark_already_contacted(self, job_id: str) -> None:
        self._finish_reservation(job_id, 'already_contacted')

    def mark_rejected(self, job_id: str) -> None:
        self._finish_reservation(job_id, 'rejected')

    def _finish_reservation(self, job_id: str, outcome: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE greetings SET outcome=?, updated_at=? WHERE job_id=? AND outcome='unknown'",
                (outcome, datetime.now(timezone.utc).isoformat(), job_id),
            )
