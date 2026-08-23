"""Append-only JSONL lake with DuckDB read views.

Why JSONL and not a database: the collector must survive an ungraceful kill at
any instant without corrupting history, must be appendable from several
processes, and the schema of upstream payloads drifts without notice. Line-
delimited JSON gives all three for free. DuckDB reads it directly, and
`compact()` folds closed days into Parquet once they stop changing.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb

from ..util.log import get
from ..util.timeutil import now, utc

log = get("degen.lake")

DEFAULT_ROOT = Path(os.getenv("DEGEN_DATA", "data/lake"))

# Datasets the system writes. Each is a directory of day-partitioned shards.
DISCOVERIES = "discoveries"   # one row the first time we ever see a mint
SNAPSHOTS = "snapshots"       # repeated observations of a mint over time
OHLCV = "ohlcv"               # candles pulled from GeckoTerminal
SAFETY = "safety"             # rugcheck / on-chain audit results
TRADES = "trades"             # our own fills (paper or live)
EVENTS = "events"             # decisions, rejections, kills - the audit trail


class Lake:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ---------------- paths ----------------

    def dataset_dir(self, dataset: str) -> Path:
        p = self.root / dataset
        p.mkdir(parents=True, exist_ok=True)
        return p

    def shard_path(self, dataset: str, ts: float | None = None) -> Path:
        day = utc(ts if ts is not None else now()).strftime("%Y-%m-%d")
        return self.dataset_dir(dataset) / f"{day}.jsonl"

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    # ---------------- write ----------------

    def append(self, dataset: str, row: dict[str, Any]) -> None:
        self.append_many(dataset, (row,))

    def append_many(self, dataset: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        path = self.shard_path(dataset)
        blob = "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in rows)
        with self._lock(str(path)):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
        return len(rows)

    # ---------------- read ----------------

    def shards(self, dataset: str) -> list[Path]:
        d = self.root / dataset
        if not d.exists():
            return []
        return sorted(list(d.glob("*.jsonl")) + list(d.glob("*.parquet")))

    def iter_rows(self, dataset: str) -> Iterator[dict[str, Any]]:
        for shard in self.shards(dataset):
            if shard.suffix != ".jsonl":
                continue
            with open(shard, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn final line from a killed process

    def count(self, dataset: str) -> int:
        total = 0
        for shard in self.shards(dataset):
            if shard.suffix == ".jsonl":
                with open(shard, "rb") as fh:
                    total += sum(1 for _ in fh)
        return total

    def con(self) -> duckdb.DuckDBPyConnection:
        """A DuckDB connection with one view per dataset."""
        con = duckdb.connect()
        con.execute("SET enable_progress_bar=false")
        for dataset in (DISCOVERIES, SNAPSHOTS, OHLCV, SAFETY, TRADES, EVENTS):
            shards = self.shards(dataset)
            jsonl = [str(p) for p in shards if p.suffix == ".jsonl"]
            parq = [str(p) for p in shards if p.suffix == ".parquet"]
            parts = []
            if jsonl:
                parts.append(f"SELECT * FROM read_json_auto({jsonl!r}, union_by_name=true, ignore_errors=true)")
            if parq:
                parts.append(f"SELECT * FROM read_parquet({parq!r}, union_by_name=true)")
            if not parts:
                continue
            try:
                con.execute(f"CREATE OR REPLACE VIEW {dataset} AS " + " UNION ALL BY NAME ".join(parts))
            except duckdb.Error as exc:
                log.warning("could not build view %s: %s", dataset, exc)
        return con

    def df(self, dataset: str):
        import pandas as pd

        rows = list(self.iter_rows(dataset))
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ---------------- maintenance ----------------

    def compact(self, dataset: str, keep_today: bool = True) -> list[Path]:
        """Fold closed daily JSONL shards into Parquet. Idempotent."""
        today = utc(now()).strftime("%Y-%m-%d")
        written: list[Path] = []
        for shard in self.shards(dataset):
            if shard.suffix != ".jsonl":
                continue
            if keep_today and shard.stem == today:
                continue
            out = shard.with_suffix(".parquet")
            con = duckdb.connect()
            try:
                con.execute(
                    "COPY (SELECT * FROM read_json_auto(?, union_by_name=true, ignore_errors=true)) "
                    "TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                    [str(shard), str(out)],
                )
            except duckdb.Error as exc:
                log.warning("compact %s failed: %s", shard, exc)
                continue
            finally:
                con.close()
            shard.unlink()
            written.append(out)
            log.info("compacted %s -> %s", shard.name, out.name)
        return written


_default: Lake | None = None


def lake() -> Lake:
    global _default
    if _default is None:
        _default = Lake()
    return _default
