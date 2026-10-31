"""Preview images of the plate for the printer's screen (embedded in G-code), drawn without OpenGL.

Flat-shaded triangles in each model's filament colour, seen from the front and above, painted back to
front with Pillow; transparent background, as slicers send them."""
from __future__ import annotations

import base64
import io
import re

import numpy as np
from PIL import Image, ImageDraw

MIN_PIXEL_AREA = 0.02           # triangles smaller than this on screen add nothing


def parse_sizes(text: str) -> list[tuple[int, int, str]]:
    """'140x140/PNG, 260x260/PNG' -> [(140, 140, 'PNG'), (260, 260, 'PNG')]."""
    out = []
    for m in re.finditer(r"(\d+)\s*[x*]\s*(\d+)(?:\s*/\s*(\w+))?", text or ""):
        w, h = int(m.group(1)), int(m.group(2))
        if 8 <= w <= 1024 and 8 <= h <= 1024:
            out.append((w, h, (m.group(3) or "PNG").upper()))
    return out


def render(meshes: list, colours: list[tuple[float, float, float]], width: int, height: int,
           supersample: int = 2) -> Image.Image:
    """RGBA image of the models (``colours[i]`` is RGB 0..1 for ``meshes[i]``)."""
    sw, sh = width * supersample, height * supersample
    img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    tris, cols = [], []
    for m, c in zip(meshes, colours):
        v = np.asarray(m.vertices, dtype=np.float64)
        f = np.asarray(m.faces, dtype=np.int64)
        if len(f) == 0:
            continue
        tris.append(v[f])
        cols.append(np.repeat(np.asarray(c, dtype=np.float64)[None, :], len(f), axis=0))
    if not tris:
        return img.resize((width, height), Image.LANCZOS)
    t = np.concatenate(tris)
    c = np.concatenate(cols)

    # view: from the front (-Y), 35° above, turned 30° (an iso-like view)
    yaw, pitch = np.radians(30.0), np.radians(35.0)
    rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
    rot = rx @ rz
    centre = (t.reshape(-1, 3).min(axis=0) + t.reshape(-1, 3).max(axis=0)) / 2
    p = (t - centre) @ rot.T                    # x right, y depth (away), z up
    normals = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = (lengths > 1e-12) & (normals[:, 1] < 0)     # facing the camera (camera looks along +y)
    p, c, normals, lengths = p[keep], c[keep], normals[keep], lengths[keep]
    if len(p) == 0:
        return img.resize((width, height), Image.LANCZOS)
    n = normals / lengths[:, None]
    light = np.array([-0.35, -0.75, 0.55])
    light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
    base = 0.16 + 0.84 * c                      # black filament still shows its shape
    rgb = np.clip(base * shade[:, None] * 255, 0, 255).astype(np.uint8)

    xy = p[:, :, [0, 2]]
    lo, hi = xy.reshape(-1, 2).min(axis=0), xy.reshape(-1, 2).max(axis=0)
    span = max(hi[0] - lo[0], hi[1] - lo[1], 1e-6)
    scale = 0.86 * min(sw, sh) / span
    mid = (lo + hi) / 2
    sx = (xy[:, :, 0] - mid[0]) * scale + sw / 2
    sy = sh / 2 - (xy[:, :, 1] - mid[1]) * scale
    area = np.abs((sx[:, 1] - sx[:, 0]) * (sy[:, 2] - sy[:, 0]) - (sx[:, 2] - sx[:, 0]) * (sy[:, 1] - sy[:, 0])) / 2
    visible = np.nonzero(area >= MIN_PIXEL_AREA)[0]
    order = visible[np.argsort(-p[visible, :, 1].mean(axis=1))]      # farthest first
    draw = ImageDraw.Draw(img)
    for k in order:
        col = tuple(int(v) for v in rgb[k]) + (255,)
        draw.polygon([(sx[k, 0], sy[k, 0]), (sx[k, 1], sy[k, 1]), (sx[k, 2], sy[k, 2])], fill=col, outline=col)
    return img.resize((width, height), Image.LANCZOS)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def gcode_blocks(meshes: list, colours: list, sizes_text: str) -> str:
    """Thumbnail comment blocks in the PrusaSlicer / OrcaSlicer / Creality Print layout:
    ; THUMBNAIL_BLOCK_START / ; thumbnail begin WxH LEN / base64 in 78-character lines / ; thumbnail end."""
    out = []
    for w, h, fmt in parse_sizes(sizes_text):
        if fmt not in ("PNG", "CR_PNG"):
            fmt = "PNG"
        img = render(meshes, colours, w, h)
        data = base64.b64encode(png_bytes(img)).decode("ascii")
        out.append("; THUMBNAIL_BLOCK_START")
        out.append(";")
        if fmt == "CR_PNG":                    # Creality's variant: rows that hold the picture + layer count
            alpha = np.asarray(img)[:, :, 3]
            rows = np.nonzero(alpha.max(axis=1) > 0)[0]
            first, last = (int(rows[0]), int(rows[-1])) if len(rows) else (0, 0)
            out.append(f"; png begin {w}*{h} {len(data)} {first} {last} 0")
        else:
            out.append(f"; thumbnail begin {w}x{h} {len(data)}")
        for k in range(0, len(data), 78):
            out.append("; " + data[k:k + 78])
        out.append("; png end" if fmt == "CR_PNG" else "; thumbnail end")
        out.append("; THUMBNAIL_BLOCK_END")
        out.append("")
    return "\n".join(out)


def read_gcode_thumbnails(text: str) -> list[tuple[int, int, bytes]]:
    """The PNG images embedded in a G-code file (for checks and previews)."""
    out = []
    for m in re.finditer(r"; (?:thumbnail|png) begin (\d+)[x*](\d+) (\d+)[^\n]*\n(.*?); (?:thumbnail|png) end", text, re.S):
        data = "".join(line[2:].strip() for line in m.group(4).splitlines() if line.startswith("; "))
        out.append((int(m.group(1)), int(m.group(2)), base64.b64decode(data)))
    return out
