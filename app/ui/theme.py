"""Runtime light/dark theming.

A single QSS template is rendered with a colour palette so the two themes
stay in lockstep. The dark palette reproduces the original ``style.qss``
exactly; the light palette is a readable inverse. ``build_qss`` returns the
stylesheet string for a given theme name.
"""

from __future__ import annotations

from string import Template

THEMES = ("dark", "light")
DEFAULT_THEME = "dark"

# Order mirrors the original style.qss so the dark palette is a 1:1 port.
_DARK = {
    "bg": "#1e1f26",
    "fg": "#e6e6e6",
    "pane_border": "#2c2e38",
    "pane_bg": "#23242d",
    "tab_bg": "#23242d",
    "tab_fg": "#b6b9c4",
    "accent": "#3a6df0",
    "accent_hover": "#4f7df5",
    "accent_pressed": "#2f5ad0",
    "tab_hover": "#2f313c",
    "btn_disabled_bg": "#3a3d4a",
    "btn_disabled_fg": "#7c7f8c",
    "danger": "#e0483e",
    "danger_hover": "#f2574d",
    "danger_pressed": "#c23a31",
    "ghost_border": "#3a3d4a",
    "ghost_fg": "#d6d8e0",
    "input_bg": "#2a2c36",
    "input_border": "#3a3d4a",
    "table_bg": "#23242d",
    "table_alt": "#262833",
    "grid": "#2c2e38",
    "selection_bg": "#2f4b9e",
    "selection_fg": "#ffffff",
    "header_bg": "#2a2c36",
    "header_fg": "#aeb2bf",
    "checkbox_border": "#4a4d5a",
    "title_fg": "#ffffff",
    "subtitle_fg": "#9296a3",
    "progress_bg": "#2a2c36",
    "scrollbar_handle": "#3a3d4a",
    "scrollbar_handle_hover": "#4a4d5a",
    "tooltip_bg": "#2a2c36",
    "statusbar_bg": "#181920",
    "banner_bg": "#3a2f1a",
    "banner_border": "#6b531f",
    "banner_fg": "#f2c14e",
}

_LIGHT = {
    "bg": "#f3f4f7",
    "fg": "#1e1f26",
    "pane_border": "#d4d7e0",
    "pane_bg": "#ffffff",
    "tab_bg": "#e7e9f0",
    "tab_fg": "#4a4d5a",
    "accent": "#3a6df0",
    "accent_hover": "#4f7df5",
    "accent_pressed": "#2f5ad0",
    "tab_hover": "#dfe2ec",
    "btn_disabled_bg": "#c9ccd6",
    "btn_disabled_fg": "#9296a3",
    "danger": "#e0483e",
    "danger_hover": "#f2574d",
    "danger_pressed": "#c23a31",
    "ghost_border": "#c2c5d0",
    "ghost_fg": "#2a2c36",
    "input_bg": "#ffffff",
    "input_border": "#c2c5d0",
    "table_bg": "#ffffff",
    "table_alt": "#f3f4f7",
    "grid": "#e2e4ec",
    "selection_bg": "#c2d4ff",
    "selection_fg": "#14151a",
    "header_bg": "#eceef4",
    "header_fg": "#5a5d6a",
    "checkbox_border": "#b0b3c0",
    "title_fg": "#14151a",
    "subtitle_fg": "#6a6d7a",
    "progress_bg": "#e2e4ec",
    "scrollbar_handle": "#c2c5d0",
    "scrollbar_handle_hover": "#a8abb8",
    "tooltip_bg": "#ffffff",
    "statusbar_bg": "#e7e9f0",
    "banner_bg": "#fff4d6",
    "banner_border": "#e0b651",
    "banner_fg": "#8a6d1a",
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}

_TEMPLATE = Template(
    """
* {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}

QMainWindow, QWidget {
    background-color: $bg;
    color: $fg;
}

QTabWidget::pane {
    border: 1px solid $pane_border;
    border-radius: 6px;
    top: -1px;
    background: $pane_bg;
}

QTabBar::tab {
    background: $tab_bg;
    color: $tab_fg;
    padding: 9px 18px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: $accent;
    color: #ffffff;
}

QTabBar::tab:hover:!selected {
    background: $tab_hover;
    color: $fg;
}

QPushButton {
    background-color: $accent;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}

QPushButton:hover { background-color: $accent_hover; }
QPushButton:pressed { background-color: $accent_pressed; }
QPushButton:disabled { background-color: $btn_disabled_bg; color: $btn_disabled_fg; }

QPushButton#danger { background-color: $danger; }
QPushButton#danger:hover { background-color: $danger_hover; }
QPushButton#danger:pressed { background-color: $danger_pressed; }

QPushButton#ghost {
    background-color: transparent;
    border: 1px solid $ghost_border;
    color: $ghost_fg;
}
QPushButton#ghost:hover { background-color: $tab_hover; }

QLineEdit, QComboBox {
    background-color: $input_bg;
    border: 1px solid $input_border;
    border-radius: 6px;
    padding: 7px 10px;
    color: $fg;
    selection-background-color: $accent;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid $accent; }

QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: $input_bg;
    border: 1px solid $input_border;
    selection-background-color: $accent;
    outline: none;
}

QTableWidget, QTreeWidget, QTableView {
    background-color: $table_bg;
    alternate-background-color: $table_alt;
    gridline-color: $grid;
    border: 1px solid $grid;
    border-radius: 6px;
    selection-background-color: $selection_bg;
    selection-color: $selection_fg;
    outline: none;
}

QHeaderView::section {
    background-color: $header_bg;
    color: $header_fg;
    padding: 7px 8px;
    border: none;
    border-right: 1px solid $grid;
    border-bottom: 1px solid $grid;
    font-weight: 600;
}

QTableWidget::item, QTreeWidget::item, QTableView::item { padding: 4px 6px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid $checkbox_border;
    border-radius: 4px;
    background: $input_bg;
}
QCheckBox::indicator:checked {
    background: $accent;
    border: 1px solid $accent;
    image: none;
}

QLabel#title { font-size: 18px; font-weight: 700; color: $title_fg; }
QLabel#subtitle { color: $subtitle_fg; }
QLabel#statusbadge { color: $subtitle_fg; }

QProgressBar {
    background-color: $progress_bg;
    border: none;
    border-radius: 6px;
    text-align: center;
    color: $fg;
    height: 18px;
}
QProgressBar::chunk { background-color: $accent; border-radius: 6px; }

QScrollBar:vertical {
    background: transparent; width: 12px; margin: 0;
}
QScrollBar::handle:vertical {
    background: $scrollbar_handle; border-radius: 6px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: $scrollbar_handle_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QToolTip {
    background-color: $tooltip_bg;
    color: $fg;
    border: 1px solid $input_border;
    padding: 6px;
    border-radius: 4px;
}

QStatusBar { background: $statusbar_bg; color: $subtitle_fg; }

QMenuBar { background: $statusbar_bg; color: $fg; }
QMenuBar::item:selected { background: $accent; color: #ffffff; }
QMenu { background: $input_bg; color: $fg; border: 1px solid $input_border; }
QMenu::item:selected { background: $accent; color: #ffffff; }

QFrame#banner {
    background-color: $banner_bg;
    border: 1px solid $banner_border;
    border-radius: 6px;
}
QLabel#bannerText { color: $banner_fg; }
"""
)


def normalize(theme: str) -> str:
    """Return a valid theme name, falling back to the default."""
    name = (theme or "").strip().lower()
    return name if name in _PALETTES else DEFAULT_THEME


def build_qss(theme: str = DEFAULT_THEME) -> str:
    """Render the stylesheet for ``theme`` ('dark' or 'light')."""
    palette = _PALETTES[normalize(theme)]
    return _TEMPLATE.substitute(palette)
