"""Job report for a plate (as GrabCAD Print's tray report): printer, material, settings that matter, a
picture of the plate and the per-model estimate - as HTML, which the GUI turns into a PDF."""
from __future__ import annotations

import base64
import html
import time

from .estimate import PlateEstimate, fmt_time


def plate_picture_png(objects: list, colours: list | None = None, size: int = 360) -> bytes:
    from .thumbnail import png_bytes, render
    parts = [o for o in objects if o.role != "cutter"]
    if not parts:
        return b""
    meshes = [o.world_mesh() for o in parts]
    cols = colours or [(0.72, 0.74, 0.8)] * len(meshes)
    return png_bytes(render(meshes, cols, size, size))


def build_html(est: PlateEstimate, cfg, printer_name: str, material_name: str, job_name: str,
               picture_png: bytes = b"", file_path: str = "", currency: str = "₪", picture_src: str = "") -> str:
    e = html.escape
    unit = "ml" if est.technology == "sla" else "cm³"
    rows = "".join(
        f"<tr><td>{e(r.name)}</td><td>{r.model_ml:.2f}</td><td>{r.support_ml:.2f}</td><td>{r.total_ml:.2f}</td>"
        f"<td>{r.weight_g:.1f}</td><td>{currency}{r.cost:.2f}</td><td>{r.height:.1f}</td></tr>" for r in est.rows)
    if est.technology == "sla":
        settings = [("Layer height", f"{cfg['sla_layer_height'] * 1000:.0f} µm"), ("Exposure", f"{cfg['exposure_time']:.2f} s"),
                    ("Bottom layers", f"{cfg['bottom_layers']} × {cfg['bottom_exposure_time']:.1f} s"),
                    ("Supports", "on" if cfg["support_enable"] else "off"), ("Raft", cfg["sla_raft_type"]),
                    ("Hollow", "on" if cfg["hollow_enable"] or cfg["hollow_infill"] != "none" else "off")]
    else:
        settings = [("Layer height", f"{cfg['layer_height']:.2f} mm"), ("Infill", f"{cfg['infill_density']:.0f} %"),
                    ("Walls", str(cfg["wall_count"])), ("Nozzle", f"{cfg['nozzle_temp']} °C"),
                    ("Supports", "on" if cfg["support_enable"] else "off")]
    srows = "".join(f"<tr><td>{e(k)}</td><td>{e(str(v))}</td></tr>" for k, v in settings)
    if picture_src:                       # an image resource registered by the caller (Qt's PDF writer)
        pic = f'<img src="{e(picture_src)}" width="260">'
    elif picture_png:
        pic = f'<img src="data:image/png;base64,{base64.b64encode(picture_png).decode()}" width="260">'
    else:
        pic = ""
    note = "Totals from the sliced plate." if est.calibrated else "Estimate before slicing (approximate)."
    return f"""<html><head><meta charset="utf-8"><style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #111; }}
h1 {{ font-size: 20px; margin: 0 0 4px 0; color: #eb1a91; }}
.sub {{ color: #666; font-size: 11px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 11px; margin-top: 8px; }}
th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: left; }}
th {{ background: #f5f5f7; }}
.tot td {{ font-weight: bold; background: #fafafa; }}
</style></head><body>
<h1>UniSlicer job report</h1>
<div class="sub">{e(job_name)} · {e(time.strftime('%Y-%m-%d %H:%M'))}{(' · ' + e(file_path)) if file_path else ''}</div>
<table><tr><td width="62%">
<table>
<tr><th colspan="2">Job</th></tr>
<tr><td>Printer</td><td>{e(printer_name)}</td></tr>
<tr><td>Material</td><td>{e(material_name)}</td></tr>
<tr><td>Models</td><td>{len(est.rows)}</td></tr>
<tr><td>Print time</td><td>{fmt_time(est.time_s)}</td></tr>
<tr><td>Layers</td><td>{est.layers}</td></tr>
<tr><td>Material total</td><td>{est.total_ml:.2f} {unit} · {est.weight_g:.1f} g</td></tr>
<tr><td>Cost</td><td>{currency}{est.cost:.2f}</td></tr>
</table>
<table><tr><th colspan="2">Settings</th></tr>{srows}</table>
</td><td align="center">{pic}</td></tr></table>
<table>
<tr><th>Model</th><th>Model {unit}</th><th>Supports {unit}</th><th>Total {unit}</th><th>Weight g</th><th>Cost</th><th>Height mm</th></tr>
{rows}
<tr class="tot"><td>Total</td><td></td><td></td><td>{est.total_ml:.2f}</td><td>{est.weight_g:.1f}</td><td>{currency}{est.cost:.2f}</td><td></td></tr>
</table>
<div class="sub">{note}</div>
</body></html>"""
