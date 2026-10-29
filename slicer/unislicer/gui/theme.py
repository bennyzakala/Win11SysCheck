"""Colours, stylesheet and icons for the GUI."""
from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import QByteArray, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

BG = "#14171d"
SURFACE = "#1b1f27"
SURFACE_2 = "#222732"
SURFACE_3 = "#2a303d"
BORDER = "#323949"
TEXT = "#e8ebf2"
TEXT_DIM = "#9aa3b5"
TEXT_FAINT = "#6b7385"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9fff"
ACCENT_PRESSED = "#3a74e0"
SUCCESS = "#2ecc8f"
WARNING = "#f5b544"
DANGER = "#ff5d6c"

# viewport colours (RGB 0..1)
VIEW_BG_TOP = (0.125, 0.140, 0.172)
VIEW_BG_BOTTOM = (0.070, 0.078, 0.098)
MODEL_COLOR = (0.36, 0.60, 1.00)
MODEL_SELECTED = (0.47, 0.82, 1.00)
MODEL_OUTSIDE = (1.00, 0.42, 0.45)
SUPPORT_COLOR = (0.66, 0.70, 0.78)

FEATURE_COLORS = {
    "outer_wall": "#ff8a4c",
    "inner_wall": "#ffd166",
    "skin": "#ef476f",
    "infill": "#2ed3a5",
    "support": "#9aa6c4",
    "interface": "#7b86f0",
    "skirt": "#c8d0e8",
    "travel": "#47c6f0",
}
FEATURE_ORDER = ["outer_wall", "inner_wall", "skin", "infill", "support", "interface", "skirt", "travel"]


def feature_rgb(kind: str) -> tuple[float, float, float]:
    c = QColor(FEATURE_COLORS[kind])
    return c.redF(), c.greenF(), c.blueF()


STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}
QFrame#Card, QWidget#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#TopBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame#SideBar {{ background: {SURFACE}; border: none; }}
QLabel {{ background: transparent; }}
QWidget#Overlay {{ background: transparent; }}
QLabel#AppTitle {{ font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#AppSubtitle {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#SectionTitle {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Faint {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#StatValue {{ font-size: 17px; font-weight: 700; }}
QLabel#StatLabel {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#Warning {{ color: {WARNING}; }}
QLabel#Error {{ color: {DANGER}; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #3f4759; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE}; border-color: {SURFACE_3}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; font-weight: 700; font-size: 14px; padding: 11px 18px; border-radius: 10px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background: #2c3d5e; color: #8ea3c8; }}
QPushButton#Success {{ background: {SUCCESS}; border: none; color: #08251a; font-weight: 700; font-size: 14px; padding: 11px 18px; border-radius: 10px; }}
QPushButton#Success:hover {{ background: #45dba2; }}
QPushButton#Success:disabled {{ background: #1f4a3a; color: #6fa58f; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px; color: {TEXT}; }}
QToolButton:hover {{ background: {SURFACE_3}; border-color: {BORDER}; }}
QToolButton:pressed, QToolButton:checked {{ background: #263552; border-color: {ACCENT}; }}
QToolButton#TextTool {{ padding: 6px 10px; }}
QToolButton#Reset {{ padding: 2px; border-radius: 6px; }}
QToolButton#SectionHeader {{ border: none; border-radius: 10px; padding: 10px 12px; font-weight: 600; font-size: 13px; text-align: left; }}
QToolButton#SectionHeader:hover {{ background: {SURFACE_2}; }}
QToolButton#SectionHeader:checked {{ background: transparent; border: none; }}

QFrame#Segmented {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#Segmented QPushButton {{ background: transparent; border: none; border-radius: 8px; padding: 7px 18px; color: {TEXT_DIM}; font-weight: 600; }}
QFrame#Segmented QPushButton:checked {{ background: {ACCENT}; color: white; }}
QFrame#Segmented QPushButton:hover:!checked {{ color: {TEXT}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {ACCENT}; color: {TEXT};
}}
QAbstractSpinBox QLineEdit {{ padding: 0; margin: 0; border: none; background: transparent; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; height: 0px; border: none; subcontrol-origin: border; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit {{ font-family: "JetBrains Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 12px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url(@ASSETS@/chevron.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; padding: 4px; }}
QComboBox#ProfileCombo {{ padding: 8px 10px; font-weight: 600; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 36px; height: 20px; image: url(@ASSETS@/toggle_off.svg); }}
QCheckBox::indicator:checked {{ image: url(@ASSETS@/toggle_on.svg); }}
QCheckBox::indicator:disabled {{ image: url(@ASSETS@/toggle_disabled.svg); }}

QListWidget {{ background: transparent; border: none; padding: 2px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: #263552; color: {TEXT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4254; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QSlider::groove:vertical {{ background: {SURFACE_3}; width: 6px; border-radius: 3px; }}
QSlider::sub-page:vertical {{ background: {SURFACE_3}; border-radius: 3px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:vertical {{ background: white; border: 3px solid {ACCENT}; height: 14px; width: 14px; margin: 0 -7px; border-radius: 10px; }}
QSlider::groove:horizontal {{ background: {SURFACE_3}; height: 6px; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 3px solid {ACCENT}; height: 14px; width: 14px; margin: -7px 0; border-radius: 10px; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMessageBox, QDialog {{ background: {SURFACE}; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def _asset_files() -> dict[str, str]:
    toggle = ('<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20" viewBox="0 0 36 20">'
              '<rect x="0.5" y="0.5" width="35" height="19" rx="9.5" fill="{bg}" stroke="{border}"/>'
              '<circle cx="{cx}" cy="10" r="7" fill="{knob}"/></svg>')
    return {
        "toggle_on.svg": toggle.format(bg=ACCENT, border=ACCENT, cx=26, knob="#ffffff"),
        "toggle_off.svg": toggle.format(bg=SURFACE_3, border=BORDER, cx=10, knob="#8b93a6"),
        "toggle_disabled.svg": toggle.format(bg=SURFACE, border=SURFACE_3, cx=10, knob="#4a5163"),
        "chevron.svg": ('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{TEXT_DIM}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
                        '<path d="m6 9 6 6 6-6"/></svg>'),
    }


def _write_assets() -> str | None:
    """Write small SVGs that the stylesheet references by file path; None if no folder is writable."""
    candidates = []
    app_data = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if app_data:
        candidates.append(os.path.join(app_data, "assets"))
    candidates.append(os.path.join(tempfile.gettempdir(), f"unislicer-assets-{os.getpid()}"))
    for folder in candidates:
        try:
            os.makedirs(folder, exist_ok=True)
            for name, content in _asset_files().items():
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write(content)
            return folder.replace(os.sep, "/")
        except OSError as exc:
            print(f"cannot write UI assets to {folder}: {exc}", file=sys.stderr)
    return None


def apply(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE_2),
                        (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT), (QPalette.Button, SURFACE_2),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.ToolTipBase, SURFACE_3), (QPalette.ToolTipText, TEXT),
                        (QPalette.PlaceholderText, TEXT_FAINT)]:
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Noto Sans Hebrew", "Noto Sans", "Rubik", "Cantarell", "DejaVu Sans"):
        if name in families:
            font = QFont(name, 10)
            font.setHintingPreference(QFont.PreferNoHinting)
            app.setFont(font)
            break
    assets = _write_assets()
    if assets:
        sheet = STYLESHEET.replace("@ASSETS@", assets)
    else:  # plain checkboxes and combo arrows rather than failing to start
        sheet = "\n".join(line for line in STYLESHEET.splitlines() if "@ASSETS@" not in line)
    app.setStyleSheet(sheet)


# --------------------------------------------------------------------- icons
_ICONS = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3 7v10a2 2 0 0 0 2 2h12.5a2 2 0 0 0 1.9-1.4L22 11H7.5a2 2 0 0 0-1.9 1.4L3 19"/>',
    "cube": '<path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9.2"/>',
    "shapes": '<circle cx="7" cy="7" r="3.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/><path d="M17 3.5 21 10h-8z"/><path d="M3.5 20.5 10 14"/>',
    "arrange": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "trash": '<path d="M4 7h16M9 7V4.5h6V7M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "layers": '<path d="M12 3 21.5 8 12 13 2.5 8z"/><path d="m2.5 12.5 9.5 5 9.5-5"/><path d="m2.5 17 9.5 5 9.5-5"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>',
    "flat": '<path d="M3 20h18"/><path d="M6 16h12l-2-9H8z"/>',
    "center": '<circle cx="12" cy="12" r="3"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/>',
    "view_iso": '<path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9.2"/>',
    "view_top": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16"/>',
    "view_front": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 15h16"/>',
    "view_side": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M15 4v16"/>',
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "overhang": '<path d="M3 20h6V9h12"/><path d="M12 12v8M16 12v8M20 12v8" stroke-dasharray="2 2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "chevron": '<path d="m9 6 6 6-6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "printer": '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/>',
    "square": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "flame": '<path d="M12 22c4 0 7-2.7 7-6.8 0-3.6-2.6-6.1-4.2-8.4-.4 2.2-1.6 3.6-3.1 4.2.3-3.6-1.4-6.6-4-8.5.2 3.2-2.7 5.8-2.7 10.1C5 19.3 8 22 12 22z"/>',
    "gauge": '<path d="M12 14 16 9"/><path d="M3.5 18a9.5 9.5 0 1 1 17 0z"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "pillar": '<path d="M5 21h14M7 21V9M17 21V9M12 21V9M4 9h16l-2-5H6z"/>',
    "anchor": '<circle cx="12" cy="5" r="2.5"/><path d="M12 7.5V21M5 13a7 7 0 0 0 14 0M8 11h8"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "code": '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "spool": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6"/>',
    "weight": '<path d="M6 8h12l2 13H4z"/><circle cx="12" cy="5" r="2.5"/>',
    "coin": '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .9-3 2s1.3 1.7 3 2 3 .9 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5M12 6.5v11"/>',
    "drop": '<path d="M12 3c3.5 4.5 6 7.7 6 11a6 6 0 0 1-12 0c0-3.3 2.5-6.5 6-11z"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


def icon(name: str, color: str = TEXT, size: int = 20) -> QIcon:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        pm.setDevicePixelRatio(scale)
        result.addPixmap(pm)
    return result


def logo_pixmap(size: int = 30) -> QPixmap:
    """Stacked-layers logo with a gradient."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#6aa8ff"/><stop offset="1" stop-color="#7b5cff"/></linearGradient></defs>
      <rect width="32" height="32" rx="8" fill="url(#g)"/>
      <g fill="none" stroke="white" stroke-width="2" stroke-linejoin="round" stroke-linecap="round">
        <path d="M16 7 25 11.5 16 16 7 11.5z" fill="white" fill-opacity="0.95"/>
        <path d="M7 16 16 20.5 25 16" opacity="0.85"/><path d="M7 20.5 16 25 25 20.5" opacity="0.6"/></g></svg>"""
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size * 2, size * 2))
    p.end()
    pm.setDevicePixelRatio(2)
    return pm
