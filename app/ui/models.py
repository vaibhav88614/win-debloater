"""Generic Qt model/view scaffolding shared by every data tab.

This module replaces the previous ``QTableWidget`` + ``QTableWidgetItem``
approach. Tabs now describe their columns declaratively with :class:`Column`
specs and hand a list of domain objects to :class:`ObjectTableModel`. A
:class:`RowFilterProxy` provides cheap text filtering and numeric sorting
without rebuilding any widgets on every keystroke.

Roles:
- ``Qt.DisplayRole``    — column text (empty for checkbox columns)
- ``Qt.CheckStateRole`` — per-row checkbox state (checkable columns only)
- ``Qt.ForegroundRole`` — optional per-cell color
- ``Qt.ToolTipRole``    — optional per-cell tooltip
- ``OBJECT_ROLE``       — the underlying domain object for the row
- ``SORT_ROLE``         — comparable sort key (number or string)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

# Custom item-data roles (kept clear of the built-in Qt roles).
SORT_ROLE = Qt.UserRole + 1
OBJECT_ROLE = Qt.UserRole + 2


def _noop_display(_obj: Any) -> str:
    return ""


@dataclass
class Column:
    """Declarative description of one table column.

    ``display`` extracts the visible string. ``sort_key`` (optional) returns a
    comparable value for numeric/custom sorting; when omitted the display text
    is used. ``foreground``/``tooltip`` are optional per-object callables.
    """

    title: str
    display: Callable[[Any], str] = _noop_display
    sort_key: Callable[[Any], Any] | None = None
    foreground: Callable[[Any], QColor | None] | None = None
    tooltip: Callable[[Any], str | None] | None = None
    checkable: bool = False
    resize: str = "interactive"  # interactive | stretch | contents
    width: int | None = None


class ObjectTableModel(QAbstractTableModel):
    """A table model backed by a flat list of arbitrary domain objects."""

    def __init__(
        self, columns: list[Column], objects: list[Any] | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self._columns: list[Column] = list(columns)
        self._objects: list[Any] = list(objects or [])
        # Checkbox state is tracked by object identity so it survives proxy
        # re-sorting/filtering (the model row order never changes mid-filter).
        self._checked_ids: set[int] = set()

    # ----- Qt required overrides ------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._objects)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        obj = self._objects[index.row()]
        col = self._columns[index.column()]

        if role == Qt.DisplayRole:
            return "" if col.checkable else col.display(obj)
        if role == OBJECT_ROLE:
            return obj
        if role == SORT_ROLE:
            if col.checkable:
                return 1 if id(obj) in self._checked_ids else 0
            if col.sort_key is not None:
                return col.sort_key(obj)
            return col.display(obj)
        if role == Qt.ForegroundRole and col.foreground is not None:
            return col.foreground(obj)
        if role == Qt.ToolTipRole and col.tooltip is not None:
            return col.tooltip(obj)
        if role == Qt.CheckStateRole and col.checkable:
            return Qt.Checked if id(obj) in self._checked_ids else Qt.Unchecked
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        col = self._columns[index.column()]
        if role == Qt.CheckStateRole and col.checkable:
            obj = self._objects[index.row()]
            # ``value`` may arrive as a Qt.CheckState enum or a plain int
            # depending on the caller; normalize both to a bool.
            checked = (value == Qt.Checked) or (value == Qt.Checked.value)
            if checked:
                self._checked_ids.add(id(obj))
            else:
                self._checked_ids.discard(id(obj))
            self.dataChanged.emit(index, index, [Qt.CheckStateRole, SORT_ROLE])
            return True
        return False

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._columns[index.column()].checkable:
            f |= Qt.ItemIsUserCheckable
        return f

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._columns[section].title
        return None

    # ----- Convenience API ------------------------------------------------

    @property
    def columns(self) -> list[Column]:
        return self._columns

    def set_objects(self, objects: list[Any]) -> None:
        """Replace all rows. Check state for vanished objects is dropped."""
        self.beginResetModel()
        self._objects = list(objects)
        present = {id(o) for o in self._objects}
        self._checked_ids &= present
        self.endResetModel()

    def objects(self) -> list[Any]:
        return list(self._objects)

    def object_at(self, index: QModelIndex) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if 0 <= row < len(self._objects):
            return self._objects[row]
        return None

    def _check_column(self) -> int:
        for i, c in enumerate(self._columns):
            if c.checkable:
                return i
        return -1

    def is_checked(self, obj: Any) -> bool:
        return id(obj) in self._checked_ids

    def checked_objects(self) -> list[Any]:
        return [o for o in self._objects if id(o) in self._checked_ids]

    def set_checked(self, objects: list[Any], checked: bool = True) -> None:
        col = self._check_column()
        if col < 0:
            return
        target = {id(o) for o in objects}
        for row, obj in enumerate(self._objects):
            if id(obj) in target:
                if checked:
                    self._checked_ids.add(id(obj))
                else:
                    self._checked_ids.discard(id(obj))
                idx = self.index(row, col)
                self.dataChanged.emit(idx, idx, [Qt.CheckStateRole, SORT_ROLE])

    def uncheck_all(self) -> None:
        col = self._check_column()
        if col < 0 or not self._checked_ids:
            return
        self._checked_ids.clear()
        top = self.index(0, col)
        bottom = self.index(max(0, self.rowCount() - 1), col)
        self.dataChanged.emit(top, bottom, [Qt.CheckStateRole, SORT_ROLE])


class RowFilterProxy(QSortFilterProxyModel):
    """Filtering/sorting proxy driven by a per-object predicate.

    ``filter_fn(obj, needle) -> bool`` decides row visibility. When omitted,
    the needle is matched against the concatenation of all column display
    strings. Sorting uses :data:`SORT_ROLE` so numeric columns sort by value.
    """

    def __init__(self, filter_fn: Callable[[Any, str], bool] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self._filter_fn = filter_fn
        self.setSortRole(SORT_ROLE)

    def setFilterString(self, text: str) -> None:  # noqa: N802 (Qt-style name)
        self._needle = (text or "").lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        idx = model.index(source_row, 0, source_parent)
        obj = model.object_at(idx)
        if obj is None:
            return True
        if self._filter_fn is not None:
            return self._filter_fn(obj, self._needle)
        for c in range(model.columnCount()):
            cidx = model.index(source_row, c, source_parent)
            text = model.data(cidx, Qt.DisplayRole) or ""
            if self._needle in str(text).lower():
                return True
        return False


# --------------------------------------------------------------------------
# View helpers
# --------------------------------------------------------------------------


def apply_columns(view, columns: list[Column]) -> None:
    """Apply per-column resize modes and initial widths to a QTableView."""
    from PySide6.QtWidgets import QHeaderView

    modes = {
        "interactive": QHeaderView.Interactive,
        "stretch": QHeaderView.Stretch,
        "contents": QHeaderView.ResizeToContents,
    }
    header = view.horizontalHeader()
    for i, col in enumerate(columns):
        header.setSectionResizeMode(i, modes.get(col.resize, QHeaderView.Interactive))
        if col.width:
            view.setColumnWidth(i, col.width)


def enable_column_menu(view, columns: list[Column]) -> None:
    """Attach a right-click header menu that toggles column visibility.

    Columns with an empty title (e.g. a checkbox column) are excluded so the
    selection mechanism can't be hidden.
    """
    from PySide6.QtWidgets import QMenu

    header = view.horizontalHeader()
    header.setContextMenuPolicy(Qt.CustomContextMenu)

    def _show(pos) -> None:
        menu = QMenu(view)
        for i, col in enumerate(columns):
            if not col.title:
                continue
            act = menu.addAction(col.title)
            act.setCheckable(True)
            act.setChecked(not view.isColumnHidden(i))
            act.toggled.connect(lambda checked, idx=i: view.setColumnHidden(idx, not checked))
        menu.exec(header.mapToGlobal(pos))

    header.customContextMenuRequested.connect(_show)


def selected_objects(view, proxy: RowFilterProxy, model: ObjectTableModel) -> list[Any]:
    """Return the domain objects for the currently selected rows."""
    out: list[Any] = []
    seen: set[int] = set()
    for pidx in view.selectionModel().selectedRows():
        sidx = proxy.mapToSource(pidx)
        obj = model.object_at(sidx)
        if obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            out.append(obj)
    return out


def object_at_pos(view, proxy: RowFilterProxy, model: ObjectTableModel, pos) -> Any:
    """Map a viewport position (e.g. from a context-menu request) to an object."""
    pidx = view.indexAt(pos)
    if not pidx.isValid():
        return None
    return model.object_at(proxy.mapToSource(pidx))


def visible_objects(view, proxy: RowFilterProxy, model: ObjectTableModel) -> list[Any]:
    """Return objects for every row currently visible through the proxy."""
    out: list[Any] = []
    for prow in range(proxy.rowCount()):
        pidx = proxy.index(prow, 0)
        obj = model.object_at(proxy.mapToSource(pidx))
        if obj is not None:
            out.append(obj)
    return out
