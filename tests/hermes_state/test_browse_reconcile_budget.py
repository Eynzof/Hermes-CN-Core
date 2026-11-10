"""The browse deadline also covers preparation before the DDL transaction."""

import sqlite3
from contextlib import closing

import pytest

import hermes_state_reconcile as reconcile
import hermes_state_repair as repair


def test_budget_expiring_during_preparation_leaves_history_untouched(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE history (message TEXT)")
        conn.execute("INSERT INTO history VALUES ('keep this message')")
        conn.commit()
    before = path.read_bytes()
    budget = reconcile._Budget(10)
    apply_barriers = repair._reapply_durability_barriers

    def expire_after_barriers(conn):
        apply_barriers(conn)
        budget.deadline = 0

    monkeypatch.setattr(repair, "_reapply_durability_barriers", expire_after_barriers)
    monkeypatch.setattr(reconcile, "_BUDGET_PROGRESS_OPS", 1)
    with pytest.raises(reconcile.BrowseBudgetExceeded):
        reconcile._apply_additive_schema(path, budget)
    assert path.read_bytes() == before
