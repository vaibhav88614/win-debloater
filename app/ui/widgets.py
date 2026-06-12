"""Reusable UI widgets and helpers."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SearchBar(QWidget):
    """A labelled search box plus a refresh button and result count."""

    search_changed = Signal(str)
    refresh_clicked = Signal()

    def __init__(self, placeholder: str = "Search...") -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self.search_changed.emit)

        self.count_label = QLabel("")
        self.count_label.setObjectName("subtitle")

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("ghost")
        self.refresh_btn.clicked.connect(self.refresh_clicked.emit)

        layout.addWidget(self.input, 1)
        layout.addSpacing(8)
        layout.addWidget(self.count_label)
        layout.addSpacing(8)
        layout.addWidget(self.refresh_btn)

    def set_count(self, shown: int, total: int) -> None:
        self.count_label.setText(f"{shown} of {total}")


class HeaderBar(QWidget):
    """Title + subtitle header for a tab."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("title")
        layout.addWidget(title_lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("subtitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)


def confirm(parent: QWidget, title: str, text: str, *, danger: bool = False) -> bool:
    """Show a yes/no confirmation dialog. Returns True if the user confirmed."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)
