"""Colours, stylesheet and icons for the GUI."""
from __future__ import annotations

import re

import os
import sys
import tempfile

from PySide6.QtCore import QByteArray, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Each theme sets the module colours below; set_theme() must run before any widget is built.
THEMES = {
    "dark": dict(
        BG="#14171d", SURFACE="#1b1f27", SURFACE_2="#222732", SURFACE_3="#2a303d", BORDER="#323949",
        BORDER_HOVER="#3f4759", TEXT="#e8ebf2", TEXT_DIM="#9aa3b5", TEXT_FAINT="#6b7385",
        ACCENT="#4f8cff", ACCENT_HOVER="#6a9fff", ACCENT_PRESSED="#3a74e0", ACCENT_SOFT="#263552",
        ACCENT_DISABLED="#2c3d5e", ACCENT_DISABLED_TEXT="#8ea3c8", ON_ACCENT="#ffffff",
        SUCCESS="#2ecc8f", WARNING="#f5b544", DANGER="#ff5d6c", OVERLAY=(27, 31, 39),
        VIEW_BG_TOP=(0.125, 0.140, 0.172), VIEW_BG_BOTTOM=(0.070, 0.078, 0.098),
        MODEL_COLOR=(0.36, 0.60, 1.00), MODEL_SELECTED=(1.00, 0.66, 0.32), MODEL_OUTSIDE=(1.00, 0.42, 0.45),
        SUPPORT_COLOR=(0.66, 0.70, 0.78), BED_COLOR=(0.105, 0.118, 0.145), GRID_COLOR=(0.30, 0.34, 0.42),
        LOGO_GRADIENT=("#6aa8ff", "#7b5cff"), DARK=True),
    "light": dict(
        BG="#eef1f6", SURFACE="#ffffff", SURFACE_2="#f3f5f9", SURFACE_3="#e3e7ef", BORDER="#d3d9e4",
        BORDER_HOVER="#b9c2d3", TEXT="#1a2030", TEXT_DIM="#4f5a70", TEXT_FAINT="#7b859a",
        ACCENT="#2f6fed", ACCENT_HOVER="#4a83f3", ACCENT_PRESSED="#2259c9", ACCENT_SOFT="#dce7fd",
        ACCENT_DISABLED="#b8c9ee", ACCENT_DISABLED_TEXT="#f2f5fc", ON_ACCENT="#ffffff",
        SUCCESS="#13a874", WARNING="#c98a12", DANGER="#e0404f", OVERLAY=(255, 255, 255),
        VIEW_BG_TOP=(0.93, 0.95, 0.98), VIEW_BG_BOTTOM=(0.80, 0.84, 0.90),
        MODEL_COLOR=(0.25, 0.50, 0.95), MODEL_SELECTED=(0.98, 0.55, 0.15), MODEL_OUTSIDE=(0.92, 0.25, 0.30),
        SUPPORT_COLOR=(0.55, 0.60, 0.68), BED_COLOR=(0.86, 0.89, 0.93), GRID_COLOR=(0.68, 0.73, 0.81),
        LOGO_GRADIENT=("#4a83f3", "#6a4cf0"), DARK=False),
    # Prodentix: the brand's magenta and black on white, sampled from the Prodentix logo
    "prodentix": dict(
        BG="#f4f4f6", SURFACE="#ffffff", SURFACE_2="#f7f7f9", SURFACE_3="#ececf0", BORDER="#dedee4",
        BORDER_HOVER="#c9c9d2", TEXT="#0b0b0c", TEXT_DIM="#4a4a52", TEXT_FAINT="#85858f",
        ACCENT="#eb1a91", ACCENT_HOVER="#f0399f", ACCENT_PRESSED="#c8127a", ACCENT_SOFT="#fde4f2",
        ACCENT_DISABLED="#f6b9db", ACCENT_DISABLED_TEXT="#ffffff", ON_ACCENT="#ffffff",
        SUCCESS="#1a9c6b", WARNING="#c48a16", DANGER="#d0303f", OVERLAY=(255, 255, 255),
        VIEW_BG_TOP=(0.985, 0.985, 0.99), VIEW_BG_BOTTOM=(0.86, 0.86, 0.89),
        MODEL_COLOR=(0.80, 0.80, 0.83), MODEL_SELECTED=(0.92, 0.10, 0.57), MODEL_OUTSIDE=(0.95, 0.55, 0.20),
        SUPPORT_COLOR=(0.55, 0.56, 0.62), BED_COLOR=(0.90, 0.90, 0.93), GRID_COLOR=(0.66, 0.66, 0.72),
        LOGO_GRADIENT=("#eb1a91", "#0b0b0c"), DARK=False, BRAND="Prodentix", BRAND_IMAGE="prodentix"),
    # Neon: an animated film's dusk - deep violet night, neon cyan light, a magenta creature, lime hills
    "neon": dict(
        BG="#0e0b2b", SURFACE="#171343", SURFACE_2="#201a55", SURFACE_3="#2b2368", BORDER="#3b3184",
        BORDER_HOVER="#6a55d8", TEXT="#f6f2ff", TEXT_DIM="#c3b8f2", TEXT_FAINT="#8a7fc6",
        ACCENT="#ff3fb4", ACCENT_HOVER="#ff6cc8", ACCENT_PRESSED="#e0219b", ACCENT_SOFT="#3d1f66",
        ACCENT_DISABLED="#4b2f74", ACCENT_DISABLED_TEXT="#c2a6e6", ON_ACCENT="#ffffff",
        SUCCESS="#8ef26b", WARNING="#ffc861", DANGER="#ff5a7a", OVERLAY=(23, 19, 67),
        VIEW_BG_TOP=(0.24, 0.15, 0.48), VIEW_BG_BOTTOM=(0.035, 0.045, 0.17),
        MODEL_COLOR=(0.30, 0.86, 1.00), MODEL_SELECTED=(1.00, 0.30, 0.74), MODEL_OUTSIDE=(1.00, 0.62, 0.25),
        SUPPORT_COLOR=(0.62, 0.55, 0.95), BED_COLOR=(0.09, 0.08, 0.27), GRID_COLOR=(0.22, 0.62, 0.95),
        LOGO_GRADIENT=("#36d6ff", "#ff3fb4"), DARK=True,
        GLOW="#36d6ff", HILL="#8ef26b",
        # an animated film's light: a warm key (the ring light), a cyan fill, a strong magenta rim, violet shade
        LIGHT={"uKey": (0.86, 0.78, 0.72), "uFill": (0.18, 0.42, 0.62), "uRim": (0.95, 0.30, 0.78),
               "uAmbLo": (0.16, 0.10, 0.30), "uAmbHi": (0.30, 0.28, 0.46)},
        EXTRA_QSS="""
QMainWindow, QDialog {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b0a26, stop:0.55 #15103f, stop:1 #241153); }}
QFrame#TopBar {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a1350, stop:0.5 #231a66, stop:1 #3a1764);
                 border-bottom: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {GLOW}, stop:1 {ACCENT}); }}
QFrame#SideBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1b1550, stop:1 #120e38); }}
QFrame#Card, QWidget#Card {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #221b5e, stop:1 #191347);
                             border: 1px solid #4a3ca0; border-radius: 16px; }}
QPushButton {{ border-radius: 12px; }}
QPushButton:hover {{ border-color: {GLOW}; }}
QPushButton#Primary, QPushButton#Success {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {GLOW}, stop:0.5 #9a5cff, stop:1 {ACCENT});
                     color: #ffffff; border: 1px solid rgba(255,255,255,0.35); border-radius: 14px; }}
QPushButton#Primary:hover, QPushButton#Success:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6fe4ff, stop:0.5 #b07cff, stop:1 {ACCENT_HOVER}); }}
QPushButton#Primary:pressed, QPushButton#Success:pressed {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1fb6e0, stop:1 {ACCENT_PRESSED}); }}
QPushButton:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff3fb4, stop:1 #b44cff); color: #ffffff; border: 1px solid #ff9ad8; }}
QToolButton:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2a66d8, stop:1 #ff3fb4); border: 1px solid {GLOW}; border-radius: 10px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {GLOW}, stop:0.5 {HILL}, stop:1 {ACCENT}); border-radius: 6px; }}
QSlider::sub-page:horizontal, QSlider::add-page:vertical {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {GLOW}, stop:1 {ACCENT}); border-radius: 3px; }}
QSlider::handle {{ background: #ffffff; border: 2px solid {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {GLOW}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255,63,180,0.42), stop:1 rgba(54,214,255,0.22)); color: #ffffff; border-radius: 8px; }}
QLabel#AppTitle {{ color: #ffffff; }}
QLabel#SectionTitle {{ color: {GLOW}; }}
QScrollBar::handle {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #6a55d8, stop:1 #ff3fb4); border-radius: 5px; }}
"""),
    # Hologram HUD: deep space-black blue, a glowing cyan readout, a teal second light - a mission-control screen
    "hud": dict(
        BG="#05080f", SURFACE="#0b1322", SURFACE_2="#0f1828", SURFACE_3="#152235", BORDER="#1d3450",
        BORDER_HOVER="#2b6f8f", TEXT="#d7f3ff", TEXT_DIM="#7fa8c4", TEXT_FAINT="#5c7e96",
        ACCENT="#2bd6ff", ACCENT_HOVER="#5fe4ff", ACCENT_PRESSED="#17a8d0", ACCENT_SOFT="#0d2b3d",
        ACCENT_DISABLED="#1c3a48", ACCENT_DISABLED_TEXT="#7fb3c8", ON_ACCENT="#04121b",
        SUCCESS="#36f0d0", WARNING="#ffcf5a", DANGER="#ff5d72", OVERLAY=(10, 20, 38),
        VIEW_BG_TOP=(0.040, 0.078, 0.150), VIEW_BG_BOTTOM=(0.014, 0.030, 0.060),
        MODEL_COLOR=(0.35, 0.78, 1.00), MODEL_SELECTED=(0.21, 0.94, 0.82), MODEL_OUTSIDE=(1.00, 0.36, 0.45),
        SUPPORT_COLOR=(0.29, 0.84, 0.91), BED_COLOR=(0.030, 0.090, 0.160), GRID_COLOR=(0.17, 0.55, 0.72),
        LOGO_GRADIENT=("#2bd6ff", "#36f0d0"), DARK=True, GLOW="#2bd6ff",
        LIGHT={"uKey": (0.55, 0.74, 0.84), "uFill": (0.12, 0.34, 0.50), "uRim": (0.20, 0.85, 0.95),
               "uAmbLo": (0.05, 0.12, 0.20), "uAmbHi": (0.12, 0.30, 0.42)},
        EXTRA_QSS="""
QMainWindow, QDialog {{ background: qlineargradient(x1:0.5, y1:0, x2:0.5, y2:1, stop:0 #0a1426, stop:0.6 #05080f, stop:1 #03050b); }}
QFrame#TopBar {{ background: rgba(9,18,34,0.94); border-bottom: 1px solid {ACCENT}; }}
QFrame#SideBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0a1322, stop:1 #070d19); }}
QFrame#Card, QWidget#Card {{ background: rgba(12,24,44,0.72); border: 1px solid {BORDER}; border-radius: 12px; }}
QPushButton {{ border-radius: 9px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#Primary, QPushButton#Success {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {SUCCESS}); color: {ON_ACCENT}; border: none; border-radius: 10px; }}
QPushButton#Primary:hover, QPushButton#Success:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #6bffe6); }}
QPushButton#Primary:pressed, QPushButton#Success:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; border: 1px solid {ACCENT}; }}
QToolButton:checked {{ border: 1px solid {ACCENT}; border-radius: 9px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {SUCCESS}); border-radius: 6px; }}
QSlider::sub-page:horizontal, QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle {{ background: #04121b; border: 2px solid {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: rgba(43,214,255,0.22); color: {TEXT}; border-radius: 8px; }}
QLabel#SectionTitle {{ color: {ACCENT}; }}
QScrollBar::handle {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1f6f8f, stop:1 {ACCENT}); border-radius: 5px; }}
"""),
    # Frosted glass: a blue-to-violet night behind lighter translucent panels and hairline white edges
    "glass": dict(
        BG="#20244f", SURFACE="#2a2e5e", SURFACE_2="#262a58", SURFACE_3="#33376a", BORDER="#44497a",
        BORDER_HOVER="#6670b0", TEXT="#eef1ff", TEXT_DIM="#bcc3ea", TEXT_FAINT="#8f97c4",
        ACCENT="#8ea8ff", ACCENT_HOVER="#a9bdff", ACCENT_PRESSED="#6f8cf0", ACCENT_SOFT="#343a72",
        ACCENT_DISABLED="#3c4278", ACCENT_DISABLED_TEXT="#cfd6ff", ON_ACCENT="#15183a",
        SUCCESS="#5fe0a8", WARNING="#ffcf7a", DANGER="#ff7a95", OVERLAY=(38, 42, 90),
        VIEW_BG_TOP=(0.160, 0.140, 0.340), VIEW_BG_BOTTOM=(0.090, 0.090, 0.240),
        MODEL_COLOR=(0.68, 0.73, 1.00), MODEL_SELECTED=(1.00, 0.78, 0.45), MODEL_OUTSIDE=(1.00, 0.48, 0.58),
        SUPPORT_COLOR=(0.80, 0.84, 1.00), BED_COLOR=(0.120, 0.120, 0.300), GRID_COLOR=(0.50, 0.52, 0.78),
        LOGO_GRADIENT=("#8ea8ff", "#c4a9ff"), DARK=True, GLOW="#a9bdff",
        LIGHT={"uKey": (0.70, 0.72, 0.84), "uFill": (0.26, 0.28, 0.44), "uRim": (0.52, 0.58, 0.95),
               "uAmbLo": (0.16, 0.16, 0.30), "uAmbHi": (0.30, 0.30, 0.46)},
        EXTRA_QSS="""
QMainWindow, QDialog {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1f4d, stop:0.5 #272a5c, stop:1 #3a2a63); }}
QFrame#TopBar {{ background: rgba(255,255,255,0.08); border-bottom: 1px solid rgba(255,255,255,0.18); }}
QFrame#SideBar {{ background: rgba(255,255,255,0.055); border: none; }}
QFrame#Card, QWidget#Card {{ background: rgba(255,255,255,0.09); border: 1px solid rgba(255,255,255,0.18); border-radius: 16px; }}
QPushButton {{ background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.16); border-radius: 12px; }}
QPushButton:hover {{ background: rgba(255,255,255,0.16); border-color: rgba(255,255,255,0.32); }}
QPushButton#Primary, QPushButton#Success {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 #c4a9ff); color: {ON_ACCENT}; border: 1px solid rgba(255,255,255,0.40); border-radius: 13px; }}
QPushButton#Primary:hover, QPushButton#Success:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT_HOVER}, stop:1 #d4bcff); }}
QPushButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; border: 1px solid rgba(255,255,255,0.4); }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 #c4a9ff); border-radius: 6px; }}
QSlider::sub-page:horizontal, QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle {{ background: #ffffff; border: 2px solid {ACCENT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.18); }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: rgba(142,168,255,0.35); color: #ffffff; border-radius: 8px; }}
QLabel#SectionTitle {{ color: #cdd5ff; }}
QScrollBar::handle {{ background: rgba(255,255,255,0.22); border-radius: 5px; }}
"""),
    # Neon cyberpunk: near-black with a magenta/cyan current running through the chrome
    "cyber": dict(
        BG="#0a0510", SURFACE="#120a1e", SURFACE_2="#180d28", SURFACE_3="#211036", BORDER="#3a1d52",
        BORDER_HOVER="#ff2bd0", TEXT="#ffe9fb", TEXT_DIM="#c79ad4", TEXT_FAINT="#8f6aa0",
        ACCENT="#ff2bd0", ACCENT_HOVER="#ff5cdc", ACCENT_PRESSED="#d416ad", ACCENT_SOFT="#331041",
        ACCENT_DISABLED="#4a2152", ACCENT_DISABLED_TEXT="#c79ad4", ON_ACCENT="#0a0510",
        SUCCESS="#49f0b0", WARNING="#ffc861", DANGER="#ff5a7a", OVERLAY=(18, 10, 30),
        VIEW_BG_TOP=(0.120, 0.040, 0.170), VIEW_BG_BOTTOM=(0.030, 0.015, 0.050),
        MODEL_COLOR=(1.00, 0.33, 0.85), MODEL_SELECTED=(0.13, 0.89, 1.00), MODEL_OUTSIDE=(1.00, 0.55, 0.25),
        SUPPORT_COLOR=(0.13, 0.89, 1.00), BED_COLOR=(0.060, 0.030, 0.120), GRID_COLOR=(0.60, 0.17, 0.68),
        LOGO_GRADIENT=("#ff2bd0", "#20e3ff"), DARK=True, GLOW="#20e3ff",
        LIGHT={"uKey": (0.80, 0.60, 0.75), "uFill": (0.10, 0.50, 0.60), "uRim": (1.00, 0.20, 0.82),
               "uAmbLo": (0.10, 0.03, 0.16), "uAmbHi": (0.22, 0.10, 0.30)},
        EXTRA_QSS="""
QMainWindow, QDialog {{ background: qlineargradient(x1:0.8, y1:0, x2:0.2, y2:1, stop:0 #15071f, stop:0.55 #0a0510, stop:1 #06030b); }}
QFrame#TopBar {{ background: #100718; border-bottom: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {GLOW}); }}
QFrame#SideBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #120a1e, stop:1 #0a0512); }}
QFrame#Card, QWidget#Card {{ background: #140a22; border: 1px solid {BORDER}; border-radius: 12px; }}
QPushButton {{ border-radius: 10px; }}
QPushButton:hover {{ border-color: {GLOW}; }}
QPushButton#Primary, QPushButton#Success {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {GLOW}); color: #ffffff; border: 1px solid rgba(255,255,255,0.30); border-radius: 12px; }}
QPushButton#Primary:hover, QPushButton#Success:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #5cf0ff); }}
QPushButton#Primary:pressed, QPushButton#Success:pressed {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_PRESSED}, stop:1 #17b8d8); }}
QPushButton:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {GLOW}); color: #ffffff; border: 1px solid {GLOW}; }}
QToolButton:checked {{ border: 1px solid {GLOW}; border-radius: 9px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {GLOW}); border-radius: 6px; }}
QSlider::sub-page:horizontal, QSlider::add-page:vertical {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {GLOW}); border-radius: 3px; }}
QSlider::handle {{ background: #ffffff; border: 2px solid {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {GLOW}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255,43,208,0.42), stop:1 rgba(32,227,255,0.22)); color: #ffffff; border-radius: 8px; }}
QLabel#SectionTitle {{ color: {GLOW}; }}
QScrollBar::handle {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {GLOW}); border-radius: 5px; }}
"""),
}
THEME_NAMES = tuple(THEMES)
# the active theme's colours (filled in by set_theme)
BG: object = None
SURFACE: object = None
SURFACE_2: object = None
SURFACE_3: object = None
BORDER: object = None
BORDER_HOVER: object = None
TEXT: object = None
TEXT_DIM: object = None
TEXT_FAINT: object = None
ACCENT: object = None
ACCENT_HOVER: object = None
ACCENT_PRESSED: object = None
ACCENT_SOFT: object = None
ACCENT_DISABLED: object = None
ACCENT_DISABLED_TEXT: object = None
ON_ACCENT: object = None
SUCCESS: object = None
WARNING: object = None
DANGER: object = None
OVERLAY: object = None
VIEW_BG_TOP: object = None
VIEW_BG_BOTTOM: object = None
MODEL_COLOR: object = None
MODEL_SELECTED: object = None
MODEL_OUTSIDE: object = None
SUPPORT_COLOR: object = None
BED_COLOR: object = None
GRID_COLOR: object = None
LOGO_GRADIENT: object = None
DARK: object = None
CURRENT = "dark"
BRAND = ""            # wordmark shown next to the logo by branded themes
LOGO_FILE = ""        # optional image (PNG/SVG/JPG) replacing the built-in logo
BRAND_IMAGE = ""      # built-in brand artwork in gui/brand/ (e.g. "prodentix")
BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand")


def app_icon() -> QIcon:
    """The program's icon (the Prodentix fox on UniSlicer's layers - tools/make_icon.py), the same in every
    theme: the window, the taskbar and the Alt+Tab list show what the exe and the shortcuts show."""
    path = os.path.join(BRAND_DIR, "app_icon.png")
    return QIcon(path) if os.path.isfile(path) else icon("layers", ACCENT, 64)


def brand_pixmap(part: str, height: int) -> QPixmap | None:
    """Built-in brand artwork (``part`` = mark | wordmark | logo) scaled to ``height`` px, or None."""
    if not BRAND_IMAGE:
        return None
    pm = QPixmap(os.path.join(BRAND_DIR, f"{BRAND_IMAGE}_{part}.png"))
    if pm.isNull():
        return None
    pm = pm.scaledToHeight(height * 2, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(2)
    return pm


def set_theme(name: str, logo_file: str = "") -> None:
    global CURRENT, BRAND, LOGO_FILE
    CURRENT = name if name in THEMES else "dark"
    values = THEMES[CURRENT]
    BRAND = values.get("BRAND", "")
    LOGO_FILE = logo_file if logo_file and os.path.isfile(logo_file) else ""
    globals().update({k: v for k, v in values.items() if k not in ("BRAND", "BRAND_IMAGE", "EXTRA_QSS")})
    global BRAND_IMAGE
    BRAND_IMAGE = values.get("BRAND_IMAGE", "")


# the light on the models in the 3D view (white key and fill, a cool rim): every theme unless it sets its own
DEFAULT_LIGHT = {"uKey": (0.72, 0.72, 0.72), "uFill": (0.28, 0.28, 0.28), "uRim": (0.121, 0.158, 0.22),
                 "uAmbLo": (0.14, 0.15, 0.18), "uAmbHi": (0.30, 0.32, 0.36)}


def light() -> dict:
    return {**DEFAULT_LIGHT, **THEMES.get(CURRENT, {}).get("LIGHT", {})}


def decorate(root) -> None:
    """Theme touches a stylesheet cannot do: in a theme with a glow colour (Neon), the main buttons get a
    soft coloured glow round them - only those few, so the window stays as quick to draw."""
    glow = THEMES.get(CURRENT, {}).get("GLOW")
    if not glow or root is None:
        return
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QPushButton
    for i, b in enumerate(root.findChildren(QPushButton)):
        if b.objectName() not in ("Primary", "Success") or b.graphicsEffect() is not None:
            continue
        fx = QGraphicsDropShadowEffect(b)
        c = QColor(glow if b.objectName() == "Primary" else ACCENT)
        c.setAlpha(170)
        fx.setColor(c)
        fx.setBlurRadius(26)
        fx.setOffset(0, 3)
        b.setGraphicsEffect(fx)


def overlay(alpha: float) -> str:
    """Background for cards floating over the 3D view."""
    r, g, b = OVERLAY
    return f"rgba({r},{g},{b},{alpha:.2f})"


set_theme("dark")

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


def stylesheet() -> str:
    """The application's style: the shared rules, then the active theme's own (gradients, glows) if it has any."""
    extra = THEMES.get(CURRENT, {}).get("EXTRA_QSS", "")
    if extra:
        names = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)}
        extra = extra.format(**names)
    return _base_stylesheet() + extra


def _base_stylesheet() -> str:
    return f"""
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
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE}; border-color: {SURFACE_3}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: {ON_ACCENT}; font-weight: 700; font-size: 14px; padding: 11px 18px; border-radius: 10px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background: {ACCENT_DISABLED}; color: {ACCENT_DISABLED_TEXT}; }}
QPushButton#Success {{ background: {SUCCESS}; border: none; color: #08251a; font-weight: 700; font-size: 14px; padding: 11px 18px; border-radius: 10px; }}
QPushButton#Success:hover {{ background: #45dba2; }}
QPushButton#Success:disabled {{ background: {SURFACE_3}; color: {TEXT_FAINT}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px; color: {TEXT}; }}
QToolButton:hover {{ background: {SURFACE_3}; border-color: {BORDER}; }}
QToolButton:pressed, QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
QToolButton#TextTool {{ padding: 6px 10px; }}
QPushButton#RowApply {{ background: transparent; border: 1.5px solid {ACCENT}; color: {ACCENT}; font-weight: 700; font-size: 11px; padding: 2px 8px; border-radius: 6px; min-height: 18px; }}
QPushButton#RowApply:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; color: {ON_ACCENT}; }}
QPushButton#RowApply[pending="true"] {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton#RowApply:pressed {{ background: {ACCENT_PRESSED}; color: {ON_ACCENT}; }}
QLabel#SliceInfo {{ color: {TEXT}; font-size: 12px; font-weight: 600; padding: 0 2px; }}
QToolButton#MaterialDefault {{ border: 1px solid {BORDER}; border-radius: 6px; padding: 2px 8px; font-size: 11px; color: {TEXT_DIM}; }}
QToolButton#MaterialDefault:hover {{ border-color: {ACCENT}; color: {TEXT}; }}
QToolButton#MaterialDefault[pending="true"] {{ border: 1.5px solid {ACCENT}; color: {ACCENT}; font-weight: 700; }}
QToolButton#Reset {{ padding: 2px; border-radius: 6px; }}
QToolButton#SectionHeader {{ border: none; border-radius: 10px; padding: 10px 12px; font-weight: 600; font-size: 13px; text-align: left; }}
QToolButton#SectionHeader:hover {{ background: {SURFACE_2}; }}
QToolButton#SectionHeader:checked {{ background: transparent; border: none; }}

QFrame#Segmented {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 11px; }}
QFrame#Segmented QPushButton {{ background: transparent; border: none; border-radius: 7px; padding: 6px 18px; margin: 1px; color: {TEXT_DIM}; font-weight: 600; }}
QFrame#Segmented QPushButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; }}
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
QToolButton#PrinterPick {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 8px 10px; font-weight: 600; color: {TEXT}; text-align: left; }}
QToolButton#PrinterPick:hover {{ border-color: {ACCENT}; }}
QToolButton#PrinterPick::menu-indicator {{ image: none; }}
QToolButton#KindTab {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 6px;
                      font-weight: 700; font-size: 11px; color: {TEXT_DIM}; }}
QToolButton#KindTab:hover {{ border-color: {BORDER_HOVER}; color: {TEXT}; }}
QToolButton#KindTab:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {ON_ACCENT}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 36px; height: 20px; image: url(@ASSETS@/toggle_off.svg); }}
QCheckBox::indicator:checked {{ image: url(@ASSETS@/toggle_on.svg); }}
QCheckBox::indicator:disabled {{ image: url(@ASSETS@/toggle_disabled.svg); }}

QListWidget {{ background: transparent; border: none; padding: 2px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QSlider::groove:vertical {{ background: {SURFACE_3}; width: 6px; border-radius: 3px; }}
QSlider::sub-page:vertical {{ background: {SURFACE_3}; border-radius: 3px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:vertical {{ background: {SURFACE}; border: 3px solid {ACCENT}; height: 14px; width: 14px; margin: 0 -7px; border-radius: 10px; }}
QSlider::groove:horizontal {{ background: {SURFACE_3}; height: 6px; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {SURFACE}; border: 3px solid {ACCENT}; height: 14px; width: 14px; margin: -7px 0; border-radius: 10px; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}
QMenuBar#MainMenu {{ background: {SURFACE}; color: {TEXT}; border-bottom: 1px solid {BORDER}; padding: 1px 6px; }}
QMenuBar#MainMenu::item {{ background: transparent; padding: 4px 12px; border-radius: 6px; }}
QMenuBar#MainMenu::item:selected {{ background: {SURFACE_3}; }}
QMenuBar#MainMenu::item:pressed {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
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
        "toggle_off.svg": toggle.format(bg=SURFACE_3, border=BORDER, cx=10, knob=TEXT_FAINT),
        "toggle_disabled.svg": toggle.format(bg=SURFACE, border=SURFACE_3, cx=10, knob=BORDER),
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
                name = f"{CURRENT}_{name}"     # one set per theme: switching themes never reuses stale colours
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
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, ON_ACCENT),
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
    sheet = stylesheet()
    if assets:
        # quoted, with forward slashes: a Windows profile path with spaces or backslashes (C:\\Users\\Benny Z\\...)
        # made the whole application stylesheet fail to parse
        base = str(assets).replace("\\", "/").replace('"', '\\"')
        sheet = re.sub(r"url\(@ASSETS@/([^)]+)\)", lambda m: f'url("{base}/{CURRENT}_{m.group(1)}")', sheet)
    else:  # plain checkboxes and combo arrows rather than failing to start
        sheet = "\n".join(line for line in sheet.splitlines() if "@ASSETS@" not in line)
    app.setStyleSheet(sheet)


# --------------------------------------------------------------------- icons
_ICONS = {
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "align": '<path d="M4 3v18"/><rect x="7" y="5" width="12" height="5" rx="1"/><rect x="7" y="14" width="8" height="5" rx="1"/>',
    "check": '<path d="M4.5 12.5 9.5 17.5 19.5 6.5"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3 7v10a2 2 0 0 0 2 2h12.5a2 2 0 0 0 1.9-1.4L22 11H7.5a2 2 0 0 0-1.9 1.4L3 19"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.9 7.5-9.5V6z"/><path d="M8 8l8 8"/>',
    "cube": '<path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9.2"/>',
    "shapes": '<circle cx="7" cy="7" r="3.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/><path d="M17 3.5 21 10h-8z"/><path d="M3.5 20.5 10 14"/>',
    "arrange": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "trash": '<path d="M4 7h16M9 7V4.5h6V7M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "layers": '<path d="M12 3 21.5 8 12 13 2.5 8z"/><path d="m2.5 12.5 9.5 5 9.5-5"/><path d="m2.5 17 9.5 5 9.5-5"/>',
    "xray": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
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
    "chevron_left": '<path d="m15 6-6 6 6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "star": '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "star_fill": '<path fill="currentColor" d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.6 15.8a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19" r="1.2"/>',
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
    "ruler": '<path d="M3 17 17 3l4 4L7 21z"/><path d="m7 13 2 2M10 10l2 2M13 7l2 2"/>',
    "move": '<path d="M12 2v20M2 12h20M12 2l-3 3M12 2l3 3M12 22l-3-3M12 22l3-3M2 12l3-3M2 12l3 3M22 12l-3-3M22 12l-3 3"/>',
    "rotate3d": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/><ellipse cx="12" cy="12" rx="9" ry="3.5"/>',
    "face": '<path d="M3 20h18"/><path d="M7 16 12 6l5 10z"/><path d="M12 6v-3"/>',
    "bulb": '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1.1 2.2h5c.1-1 .5-1.7 1.1-2.2A6 6 0 0 0 12 3z"/>',
    "sparkle": '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "compass": '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "more": '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    "grip": '<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/>'
            '<circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>',
}


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    color = color or TEXT        # resolved per call: the theme may have changed since import
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name].replace("currentColor", color)}</svg>')
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


def logo_pixmap(size: int = 30, height: int | None = None) -> QPixmap:
    """The chosen logo file (scaled to ``height``, keeping its shape) or the built-in stacked-layers logo."""
    if LOGO_FILE:
        pm = QPixmap(LOGO_FILE)
        if not pm.isNull():
            h = (height or size) * 2
            pm = pm.scaledToHeight(h, Qt.SmoothTransformation)
            if pm.width() > h * 6:
                pm = pm.scaledToWidth(h * 6, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(2)
            return pm
    c0, c1 = LOGO_GRADIENT
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="{c0}"/><stop offset="1" stop-color="{c1}"/></linearGradient></defs>
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


def replace_scroll_widget(scroll, widget) -> None:
    """Put ``widget`` in a scroll area and delete the old content later, not now: a rebuild started by a
    button inside the old content (Apply, Reset...) would otherwise delete that button while its click is
    still being handled, which crashes the program on Windows."""
    old = scroll.takeWidget()
    scroll.setWidget(widget)
    if old is not None:
        old.hide()
        old.deleteLater()
