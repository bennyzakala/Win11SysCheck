"""Pictures for the user guide (unislicer/manual_img), taken in the real window with built-in shapes only
(no customer files). Run after a visible change to the interface:

    xvfb-run -a -s "-screen 0 1920x1200x24" python tools/manual_shots.py        # Hebrew guide
    xvfb-run -a -s "-screen 0 1920x1200x24" python tools/manual_shots.py en     # English guide (manual_img/en)
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
LANG = sys.argv[1] if len(sys.argv) > 1 else "he"
OUT = os.path.join(HERE, "unislicer", "manual_img", *(["en"] if LANG == "en" else []))


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication([])
    for kind in ("question", "warning", "information", "critical"):
        setattr(QMessageBox, kind, staticmethod(lambda *a, **k: QMessageBox.Yes))
    from unislicer import primitives
    from unislicer.gui import i18n, theme
    i18n.set_language(LANG)
    from PySide6.QtCore import Qt
    app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
    theme.set_theme("light", "")
    theme.apply(app)
    from unislicer.gui.main_window import MainWindow
    os.makedirs(OUT, exist_ok=True)

    def spin(s):
        end = time.time() + s
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    w = MainWindow()
    w.resize(1600, 960)
    w.show()
    spin(0.5)
    w.store.setValue("preslice", "false")
    w.assistant_panel.hide()
    w.settings.select_printer("phrozen_sonic_ls_plus")
    spin(0.3)
    w.settings.set_value("sla_raft_type", "raft_label")
    crowns = []
    for k, name in enumerate(("DEMO-SOD260012345-044-P-16-16-1.stl", "DEMO-SOD260012346-044-P-26-26-1.stl")):
        o = w.add_mesh(primitives.crown_cap(9.5 + k, 8.0, name=name))
        o.name = name
        o.apply_world_rotation(np.diag([1.0, -1.0, -1.0]))     # opening up, as the program loads crowns
        crowns.append(o)
    bridge = w.add_mesh(primitives.demo_bridge())
    spin(0.5)
    w.arrange_objects()
    w._select_uid(None)
    w.generate_supports()
    w.wait_supports()
    spin(1.5)
    w.wait_supports()
    w.viewer.fit_objects()
    spin(0.8)
    w.grab().save(os.path.join(OUT, "main.png"))

    v = w.viewer
    o = crowns[0]
    b = o.world_bounds()
    c = (b[0] + b[1]) / 2
    v.camera.target = np.array([c[0] + 3, c[1], 4.0])
    v.camera.distance, v.camera.yaw, v.camera.pitch = 42, -60, 38
    w._select_uid(None)
    spin(0.8)
    v.grab().save(os.path.join(OUT, "supports.png"))
    v.camera.target = np.array([c[0] + 3, c[1], 0.5])
    v.camera.distance, v.camera.yaw, v.camera.pitch = 36, -90, 72
    spin(0.8)
    v.grab().save(os.path.join(OUT, "raft_label.png"))

    w.slice()
    end = time.time() + 300
    while time.time() < end and not w.preview_btn.isChecked():
        spin(0.2)
    spin(1.0)
    w.set_mode("preview")
    spin(0.5)
    w.layer_slider.setValue(int(w.layer_slider.maximum() * 0.3))    # through the crowns and the bridge
    v.fit_objects()
    v.camera.yaw, v.camera.pitch = -60, 35
    spin(1.0)
    w.grab().save(os.path.join(OUT, "preview.png"))
    w._stop_preslice()
    del bridge
    print("pictures in", OUT)


if __name__ == "__main__":
    main()
