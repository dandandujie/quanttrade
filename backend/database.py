import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import costs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "quant.db"
TZ_CN = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trading_day TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stocks_dataset ON stocks(dataset_id);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                config TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_dataset ON runs(dataset_id, id DESC);
            CREATE TABLE IF NOT EXISTS daily_bars (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                close REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL NOT NULL,
                pct REAL NOT NULL,
                turnover REAL NOT NULL,
                PRIMARY KEY(code, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trade_date);
            CREATE TABLE IF NOT EXISTS performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                pool TEXT NOT NULL,
                rank INTEGER NOT NULL,
                entry_price REAL,
                open_price REAL,
                noon_price REAL,
                close_price REAL,
                open_return REAL,
                noon_return REAL,
                close_return REAL,
                max_return REAL,
                min_return REAL,
                limit_up INTEGER,
                morning_desc TEXT NOT NULL DEFAULT '',
                afternoon_desc TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                series_json TEXT NOT NULL DEFAULT '[]',
                evaluated_at TEXT NOT NULL,
                UNIQUE(run_id, code)
            );
            CREATE INDEX IF NOT EXISTS idx_performance_run ON performance(run_id, pool, rank);
            CREATE TABLE IF NOT EXISTS sync_jobs (
                id TEXT PRIMARY KEY,
                trading_day TEXT,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL,
                dataset_id INTEGER,
                run_id INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trading_day TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL,
                reason TEXT NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_signals_day ON signals(trading_day, id DESC);
            """
        )


def create_dataset(trading_day: str, name: str, source: str, stocks: list[dict[str, Any]]) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO datasets(trading_day, name, source, created_at) VALUES (?, ?, ?, ?)",
            (trading_day, name, source, now_iso()),
        )
        dataset_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO stocks(dataset_id, code, name, payload) VALUES (?, ?, ?, ?)",
            [
                (dataset_id, stock["code"], stock["name"], json.dumps(stock, ensure_ascii=False))
                for stock in stocks
            ],
        )
    return dataset_id


def list_datasets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.*, COUNT(s.id) AS stock_count,
                   (SELECT MAX(r.id) FROM runs r WHERE r.dataset_id = d.id) AS latest_run_id
            FROM datasets d LEFT JOIN stocks s ON s.dataset_id = d.id
            GROUP BY d.id ORDER BY d.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def latest_dataset_for_day(trading_day: str, source: str = "akshare") -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, COUNT(s.id) AS stock_count
            FROM datasets d LEFT JOIN stocks s ON s.dataset_id = d.id
            WHERE d.trading_day = ? AND d.source = ?
            GROUP BY d.id ORDER BY d.id DESC LIMIT 1
            """,
            (trading_day, source),
        ).fetchone()
    return dict(row) if row else None


def get_dataset(dataset_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return dict(row) if row else None


def get_stocks(dataset_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM stocks WHERE dataset_id = ? ORDER BY id", (dataset_id,)
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def update_stocks(dataset_id: int, stocks: list[dict[str, Any]]) -> int:
    """按 (dataset_id, code) 覆盖 payload，用于给已有数据集补字段。"""
    if not stocks:
        return 0
    with connect() as conn:
        conn.executemany(
            "UPDATE stocks SET payload = ? WHERE dataset_id = ? AND code = ?",
            [
                (json.dumps(stock, ensure_ascii=False), dataset_id, stock["code"])
                for stock in stocks
            ],
        )
    return len(stocks)


def create_run(dataset_id: int, config: dict[str, Any], result: dict[str, Any]) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO runs(dataset_id, created_at, config, result) VALUES (?, ?, ?, ?)",
            (
                dataset_id,
                now_iso(),
                json.dumps(config, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            ),
        )
    return int(cursor.lastrowid)


def summarize_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    main = [row for row in rows if row["pool"] == "main"]
    complete = [row for row in main if row["status"] == "complete" and row["close_return"] is not None]
    partial = [row for row in main if row["status"] == "partial"]
    candidate_complete = [
        row for row in rows
        if row["pool"] == "candidates" and row["status"] == "complete" and row["close_return"] is not None
    ]
    return {
        "evaluated": len(complete),
        "partial": len(partial),
        "unavailable": sum(row["status"] == "unavailable" for row in rows),
        "wins": sum(row["close_return"] > 0 for row in complete),
        "win_rate": round(sum(row["close_return"] > 0 for row in complete) / len(complete) * 100, 1) if complete else None,
        "limit_up_rate": round(sum(bool(row["limit_up"]) for row in complete) / len(complete) * 100, 1) if complete else None,
        "avg_return": round(sum(row["close_return"] for row in complete) / len(complete), 2) if complete else None,
        "candidate_evaluated": len(candidate_complete),
        "candidate_avg_return": round(sum(row["close_return"] for row in candidate_complete) / len(candidate_complete), 2) if candidate_complete else None,
    }


def get_performance(run_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM performance WHERE run_id = ? ORDER BY CASE pool WHEN 'main' THEN 0 ELSE 1 END, rank",
            (run_id,),
        ).fetchall()
    return [{
        **dict(row),
        "limit_up": bool(row["limit_up"]) if row["limit_up"] is not None else None,
        "series": json.loads(row["series_json"]),
    } for row in rows]


def save_performance(run_id: int, rows: list[dict[str, Any]]) -> None:
    fields = [
        "entry_price", "open_price", "noon_price", "close_price", "open_return",
        "noon_return", "close_return", "max_return", "min_return",
    ]
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO performance(
                run_id, code, name, pool, rank, entry_price, open_price, noon_price,
                close_price, open_return, noon_return, close_return, max_return,
                min_return, limit_up, morning_desc, afternoon_desc, status, error,
                series_json, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, code) DO UPDATE SET
                pool=excluded.pool, rank=excluded.rank, entry_price=excluded.entry_price,
                open_price=excluded.open_price, noon_price=excluded.noon_price,
                close_price=excluded.close_price, open_return=excluded.open_return,
                noon_return=excluded.noon_return, close_return=excluded.close_return,
                max_return=excluded.max_return, min_return=excluded.min_return,
                limit_up=excluded.limit_up, morning_desc=excluded.morning_desc,
                afternoon_desc=excluded.afternoon_desc, status=excluded.status,
                error=excluded.error, series_json=excluded.series_json,
                evaluated_at=excluded.evaluated_at
            """,
            [
                (
                    run_id, row["code"], row["name"], row["pool"], row["rank"],
                    *(row.get(field) for field in fields),
                    None if row.get("limit_up") is None else int(row["limit_up"]),
                    row["morning_desc"], row["afternoon_desc"], row["status"], row["error"],
                    json.dumps(row["series"], ensure_ascii=False, separators=(",", ":")),
                    now_iso(),
                )
                for row in rows
            ],
        )


def _run_summary(row: sqlite3.Row, returns: list[float] | None = None) -> dict[str, Any]:
    result = json.loads(row["result"])
    config = json.loads(row["config"]) if row["config"] else {}
    evaluated = row["evaluated"] or 0
    net_values = [costs.net_return_pct(value, config) for value in (returns or [])]
    if net_values:
        wins = sum(1 for value in net_values if value > 0)
        win_rate: float | None = wins / len(net_values) * 100
        avg_return: float | None = sum(net_values) / len(net_values)
    else:
        wins = row["wins"]
        win_rate = row["win_rate"]
        avg_return = row["avg_return"]
        if avg_return is not None:
            avg_return = costs.net_return_pct(avg_return, config)
    return {
        "id": row["id"],
        "dataset_id": row["dataset_id"],
        "created_at": row["created_at"],
        "trading_day": row["trading_day"],
        "dataset_name": row["dataset_name"],
        "source": row["source"],
        "summary": result["summary"],
        "top_pick": result["main"][0] if result["main"] and result["main"][0]["is_green"] else None,
        "evaluated": evaluated,
        "wins": wins,
        "limit_ups": row["limit_ups"],
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "limit_up_rate": row["limit_up_rate"],
        "avg_return": round(avg_return, 4) if avg_return is not None else None,
        "gross_win_rate": row["win_rate"],
        "gross_avg_return": row["avg_return"],
    }


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, d.trading_day, d.name AS dataset_name, d.source,
                   COUNT(CASE WHEN p.pool = 'main' AND p.status = 'complete' THEN 1 END) AS evaluated,
                   COUNT(CASE WHEN p.pool = 'main' AND p.status = 'complete' AND p.close_return > 0 THEN 1 END) AS wins,
                   COUNT(CASE WHEN p.pool = 'main' AND p.status = 'complete' AND p.limit_up = 1 THEN 1 END) AS limit_ups,
                   ROUND(AVG(CASE WHEN p.pool = 'main' AND p.status = 'complete'
                       THEN CASE WHEN p.close_return > 0 THEN 100.0 ELSE 0.0 END END), 1) AS win_rate,
                   ROUND(AVG(CASE WHEN p.pool = 'main' AND p.status = 'complete'
                       THEN CASE WHEN p.limit_up = 1 THEN 100.0 ELSE 0.0 END END), 1) AS limit_up_rate,
                   ROUND(AVG(CASE WHEN p.pool = 'main' AND p.status = 'complete'
                       THEN p.close_return END), 2) AS avg_return
            FROM runs r JOIN datasets d ON d.id = r.dataset_id
            LEFT JOIN performance p ON p.run_id = r.id
            GROUP BY r.id ORDER BY r.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        returns_by_run: dict[int, list[float]] = {}
        if rows:
            ids = [row["id"] for row in rows]
            marks = ",".join("?" for _ in ids)
            for item in conn.execute(
                f"SELECT run_id, close_return FROM performance "
                f"WHERE pool = 'main' AND status = 'complete' AND close_return IS NOT NULL "
                f"AND run_id IN ({marks})",
                ids,
            ):
                returns_by_run.setdefault(int(item["run_id"]), []).append(float(item["close_return"]))
    return [_run_summary(row, returns_by_run.get(row["id"])) for row in rows]


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    result = json.loads(row["result"])
    result["run_id"] = row["id"]
    result["created_at"] = row["created_at"]
    result["config"] = json.loads(row["config"])
    result["performance"] = get_performance(run_id)
    result["performance_summary"] = summarize_performance(result["performance"])
    return result


def get_latest_run(dataset_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE dataset_id = ? ORDER BY id DESC LIMIT 1", (dataset_id,)
        ).fetchone()
    return get_run(int(row["id"])) if row else None


def save_daily_bars(code: str, bars: list[dict[str, Any]]) -> None:
    # turnover=0 通常是兜底源缺流通股本写出的脏数据；落库后会让后续同步
    # 永远复用“昨日换手无效”的坏缓存，因此直接拒绝写入。
    bars = [bar for bar in bars if float(bar.get("turnover") or 0) > 0]
    if not bars:
        return
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_bars(
                code, trade_date, open, close, high, low, volume, amount, pct, turnover
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (code, bar["trade_date"], bar["open"], bar["close"], bar["high"], bar["low"],
                 bar["volume"], bar["amount"], bar["pct"], bar["turnover"])
                for bar in bars
            ],
        )


def get_daily_bars(code: str, start: str, end: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_bars WHERE code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (code, start, end),
        ).fetchall()
    return [dict(row) for row in rows]


def trade_dates() -> list[str]:
    """本地日线覆盖的交易日（升序）。行情源日历不可用时作为回放兜底。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_bars ORDER BY trade_date"
        ).fetchall()
    return [str(row["trade_date"]) for row in rows]


def create_sync_job(job_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sync_jobs(id, status, message, created_at, updated_at) VALUES (?, 'queued', ?, ?, ?)",
            (job_id, "等待同步免费行情", now_iso(), now_iso()),
        )


def update_sync_job(job_id: str, **fields: Any) -> None:
    allowed = {"trading_day", "status", "progress", "total", "message", "dataset_id", "run_id", "error"}
    values = {key: value for key, value in fields.items() if key in allowed}
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as conn:
        conn.execute(
            f"UPDATE sync_jobs SET {assignments} WHERE id = ?",
            (*values.values(), job_id),
        )


def get_sync_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_latest_sync_job() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_jobs ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def save_signal(
    trading_day: str, code: str, name: str, action: str,
    price: float | None, reason: str, plan: dict[str, Any],
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO signals(trading_day, code, name, action, price, reason, plan_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trading_day, code, name, action, price, reason,
                json.dumps(plan, ensure_ascii=False), now_iso(),
            ),
        )
    return int(cursor.lastrowid)


def get_signals(trading_day: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE trading_day = ? ORDER BY id DESC LIMIT ?",
            (trading_day, limit),
        ).fetchall()
    return [
        {**dict(row), "plan": json.loads(row["plan_json"])}
        for row in rows
    ]
