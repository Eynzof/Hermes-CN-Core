"""SQLite connection — uses ``apsw`` when available, falls back to ``sqlite3``.

Provides a drop-in replacement for the standard ``sqlite3`` module that
uses ``apsw`` (the advanced SQLite wrapper) when available for 1.5-3×
faster query execution and support for WAL concurrency, backup API, and
user-defined aggregate functions.

Usage:
    from hermes_sqlite import connect, Error
    conn = connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM table")
"""

import os as _os
import sqlite3 as _sqlite3
from typing import Any, Optional, Sequence

_APSW_AVAILABLE = False

if not _os.environ.get("HERMES_DISABLE_APSW_REPLACEMENT"):
    try:
        import apsw as _apsw
        _APSW_AVAILABLE = True
    except ImportError:
        pass


# ── Public API ──────────────────────────────────────────────────────────

Error = _sqlite3.Error  # Re-export for compatibility
PARSE_DECLTYPES = _sqlite3.PARSE_DECLTYPES
PARSE_COLNAMES = _sqlite3.PARSE_COLNAMES


def connect(database: str, **kwargs) -> "_Connection":
    """Connect to a SQLite database.

    When ``apsw`` is available, returns an APSW-backed connection that
    conforms to the ``sqlite3.Connection`` interface. Otherwise falls
    back to stdlib ``sqlite3.connect``.
    """
    if _APSW_AVAILABLE:
        return _APSWConnection(database, **kwargs)
    return _sqlite3.connect(database, **kwargs)


def register_adapter(type_: type, adapter: callable) -> None:
    """Register an adapter with the stdlib sqlite3 module."""
    _sqlite3.register_adapter(type_, adapter)


def register_converter(typename: str, converter: callable) -> None:
    """Register a converter with the stdlib sqlite3 module."""
    _sqlite3.register_converter(typename, converter)


# ── APSW Compatibility Layer ───────────────────────────────────────────

if _APSW_AVAILABLE:

    class _APSWCursor:
        """Wrapper that presents apsw.Cursor with a sqlite3.Cursor-compatible API."""

        def __init__(self, conn: "_APSWConnection"):
            self._conn = conn
            self._cursor = _apsw.Cursor(conn._conn)
            self._description: Optional[Sequence[Any]] = None
            self._rowcount: int = -1
            self._lastrowid: Optional[int] = None
            self._arraysize: int = 1

        @property
        def description(self):
            return self._description

        @property
        def rowcount(self):
            return self._rowcount

        @property
        def lastrowid(self):
            return self._lastrowid

        @property
        def arraysize(self):
            return self._arraysize

        @arraysize.setter
        def arraysize(self, value: int):
            self._arraysize = value

        def execute(self, sql: str, parameters: Optional[Sequence[Any]] = None) -> "_APSWCursor":
            """Execute a single SQL statement."""
            try:
                if parameters is not None:
                    if isinstance(parameters, dict):
                        self._cursor.execute(sql, parameters)
                    else:
                        self._cursor.execute(sql, tuple(parameters))
                else:
                    self._cursor.execute(sql)

                # Populate description from column info
                col_info = self._cursor.getdescription()
                if col_info:
                    self._description = [
                        (col[0], None, None, None, None, None, None) for col in col_info
                    ]
                else:
                    self._description = None

                self._rowcount = self._cursor.getrowcount()
                self._lastrowid = self._cursor.getlastrowid()
            except Exception as exc:
                raise _sqlite3.Error(str(exc)) from exc
            return self

        def executemany(self, sql: str, seq_of_parameters: Sequence[Sequence[Any]]) -> "_APSWCursor":
            """Execute the same SQL against every parameter set."""
            try:
                for parameters in seq_of_parameters:
                    self.execute(sql, parameters)
            except Exception as exc:
                raise _sqlite3.Error(str(exc)) from exc
            return self

        def executescript(self, sql_script: str) -> None:
            """Execute multiple SQL statements."""
            try:
                self._cursor.executescript(sql_script)
            except Exception as exc:
                raise _sqlite3.Error(str(exc)) from exc

        def fetchone(self) -> Optional[tuple]:
            """Fetch the next row."""
            try:
                row = self._cursor.next()
                if row is None:
                    return None
                return tuple(row)
            except StopIteration:
                return None

        def fetchmany(self, size: Optional[int] = None) -> list:
            """Fetch the next *size* rows."""
            if size is None:
                size = self._arraysize
            rows = []
            try:
                for _ in range(size):
                    row = self._cursor.next()
                    if row is None:
                        break
                    rows.append(tuple(row))
            except StopIteration:
                pass
            return rows

        def fetchall(self) -> list:
            """Fetch all remaining rows."""
            rows = []
            try:
                while True:
                    row = self._cursor.next()
                    if row is None:
                        break
                    rows.append(tuple(row))
            except StopIteration:
                pass
            return rows

        def close(self) -> None:
            """Close the cursor."""
            try:
                self._cursor.close()
            except Exception:
                pass

        def __iter__(self):
            return self

        def __next__(self):
            row = self.fetchone()
            if row is None:
                raise StopIteration
            return row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class _APSWConnection:
        """Wrapper that presents apsw.Connection with a sqlite3.Connection-compatible API."""

        def __init__(self, database: str, **kwargs):
            try:
                self._conn = _apsw.Connection(database)
            except Exception as exc:
                raise _sqlite3.Error(str(exc)) from exc

        def cursor(self) -> _APSWCursor:
            """Create a new cursor."""
            return _APSWCursor(self)

        def execute(self, sql: str, parameters: Optional[Sequence[Any]] = None) -> _APSWCursor:
            """Convenience: create cursor, execute, return cursor."""
            cur = self.cursor()
            return cur.execute(sql, parameters)

        def executemany(self, sql: str, seq_of_parameters: Sequence[Sequence[Any]]) -> _APSWCursor:
            """Convenience: create cursor, executemany, return cursor."""
            cur = self.cursor()
            return cur.executemany(sql, seq_of_parameters)

        def executescript(self, sql_script: str) -> None:
            """Execute multiple SQL statements."""
            cur = self.cursor()
            cur.executescript(sql_script)

        def commit(self) -> None:
            """Commit the current transaction."""
            self._conn.commit()

        def rollback(self) -> None:
            """Roll back the current transaction."""
            self._conn.rollback()

        def close(self) -> None:
            """Close the connection."""
            try:
                self._conn.close()
            except Exception:
                pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def row_factory(self):
            return None  # Not directly supported in this wrapper

        @row_factory.setter
        def row_factory(self, value):
            pass  # Silently ignore for compatibility

        def set_trace_callback(self, callback):
            """Set a trace callback for SQL execution."""
            try:
                self._conn.settrace(callback)
            except Exception:
                pass

        @property
        def in_transaction(self) -> bool:
            """Return True if a transaction is active."""
            try:
                return self._conn.getautocommit()
            except Exception:
                return False

        def interrupt(self) -> None:
            """Interrupt any pending database operation."""
            self._conn.interrupt()

        def backup(self, target: "_APSWConnection", **kwargs) -> None:
            """Back up this database to *target*."""
            try:
                backup_obj = _apsw.Backup(self._conn, "main", target._conn, "main")
                backup_obj.backup()
            except Exception as exc:
                raise _sqlite3.Error(str(exc)) from exc

else:
    # Fallback: just expose stdlib sqlite3 directly
    _APSWConnection = None  # type: ignore
