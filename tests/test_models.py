"""Tests for the generic table model/view scaffolding."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor

from app.ui.models import (
    OBJECT_ROLE,
    SORT_ROLE,
    Column,
    ObjectTableModel,
    RowFilterProxy,
)


@dataclass
class Row:
    name: str
    score: int
    flagged: bool = False


def _columns():
    return [
        Column("", checkable=True),
        Column("Name", display=lambda r: r.name),
        Column("Score", display=lambda r: str(r.score), sort_key=lambda r: r.score),
        Column(
            "Flag",
            display=lambda r: "yes" if r.flagged else "no",
            foreground=lambda r: QColor("#ff0000") if r.flagged else None,
            tooltip=lambda r: "flagged!" if r.flagged else None,
        ),
    ]


@pytest.fixture
def model():
    rows = [Row("alpha", 30), Row("bravo", 10, flagged=True), Row("charlie", 20)]
    return ObjectTableModel(_columns(), rows)


def test_row_and_column_counts(model):
    assert model.rowCount() == 3
    assert model.columnCount() == 4


def test_display_role_and_checkbox_blank(model):
    # Checkbox column shows no text.
    assert model.data(model.index(0, 0), Qt.DisplayRole) == ""
    assert model.data(model.index(0, 1), Qt.DisplayRole) == "alpha"
    assert model.data(model.index(0, 2), Qt.DisplayRole) == "30"


def test_object_role_returns_domain_object(model):
    obj = model.data(model.index(1, 1), OBJECT_ROLE)
    assert obj.name == "bravo"


def test_sort_role_uses_sort_key(model):
    # Score column sorts numerically by the int value, not the string.
    assert model.data(model.index(0, 2), SORT_ROLE) == 30
    # Checkbox column sort value reflects check state (0 unchecked).
    assert model.data(model.index(0, 0), SORT_ROLE) == 0


def test_foreground_and_tooltip_roles(model):
    flagged_idx = model.index(1, 3)
    assert isinstance(model.data(flagged_idx, Qt.ForegroundRole), QColor)
    assert model.data(flagged_idx, Qt.ToolTipRole) == "flagged!"
    plain_idx = model.index(0, 3)
    assert model.data(plain_idx, Qt.ForegroundRole) is None
    assert model.data(plain_idx, Qt.ToolTipRole) is None


def test_check_toggle_via_setdata(model):
    idx = model.index(0, 0)
    assert model.data(idx, Qt.CheckStateRole) == Qt.Unchecked
    assert model.setData(idx, Qt.Checked, Qt.CheckStateRole) is True
    assert model.data(idx, Qt.CheckStateRole) == Qt.Checked
    assert model.data(idx, SORT_ROLE) == 1
    assert [o.name for o in model.checked_objects()] == ["alpha"]


def test_set_checked_and_uncheck_all(model):
    objs = model.objects()
    model.set_checked([objs[0], objs[2]], True)
    assert {o.name for o in model.checked_objects()} == {"alpha", "charlie"}
    model.uncheck_all()
    assert model.checked_objects() == []


def test_set_objects_drops_stale_checks(model):
    objs = model.objects()
    model.set_checked([objs[0]], True)
    assert len(model.checked_objects()) == 1
    # Replace with brand-new objects; old check state must not linger.
    model.set_objects([Row("delta", 5)])
    assert model.checked_objects() == []
    assert model.rowCount() == 1


def test_flags_checkable_column(model):
    chk_flags = model.flags(model.index(0, 0))
    assert chk_flags & Qt.ItemIsUserCheckable
    name_flags = model.flags(model.index(0, 1))
    assert not (name_flags & Qt.ItemIsUserCheckable)
    assert name_flags & Qt.ItemIsSelectable


def test_header_data(model):
    assert model.headerData(1, Qt.Horizontal, Qt.DisplayRole) == "Name"
    assert model.headerData(2, Qt.Horizontal, Qt.DisplayRole) == "Score"


# --------------------------- proxy ---------------------------------------


def test_proxy_default_filter_matches_any_column(model):
    proxy = RowFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterString("brav")
    assert proxy.rowCount() == 1
    proxy.setFilterString("")
    assert proxy.rowCount() == 3


def test_proxy_custom_filter_fn(model):
    proxy = RowFilterProxy(lambda r, n: n in r.name.lower())
    proxy.setSourceModel(model)
    proxy.setFilterString("a")  # alpha, bravo, charlie all contain 'a'
    assert proxy.rowCount() == 3
    proxy.setFilterString("ph")  # only alpha
    assert proxy.rowCount() == 1


def test_proxy_numeric_sort(model):
    proxy = RowFilterProxy()
    proxy.setSourceModel(model)
    proxy.sort(2, Qt.AscendingOrder)  # Score column
    # Lowest score (bravo=10) should be first.
    first = proxy.data(proxy.index(0, 1), Qt.DisplayRole)
    assert first == "bravo"
    proxy.sort(2, Qt.DescendingOrder)
    first = proxy.data(proxy.index(0, 1), Qt.DisplayRole)
    assert first == "alpha"  # 30 is highest


def test_object_at_out_of_range_returns_none(model):
    assert model.object_at(QModelIndex()) is None
