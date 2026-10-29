"""OpenGL 3.3 viewport: build plate, models, SLA supports and shaded toolpath preview."""
from __future__ import annotations

import ctypes
import math
import os
import sys

import numpy as np
from OpenGL import GL
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..mesh import Mesh
from . import theme

# ------------------------------------------------------------------ shaders
BG_VS = """#version 330 core
out vec2 vUV;
void main(){ vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); vUV = p; gl_Position = vec4(p * 2.0 - 1.0, 0.999, 1.0); }"""
BG_FS = """#version 330 core
in vec2 vUV; out vec4 frag; uniform vec3 uTop; uniform vec3 uBottom;
void main(){ float t = smoothstep(0.0, 1.0, vUV.y); vec3 c = mix(uBottom, uTop, t);
  float vig = 1.0 - 0.25 * length(vUV - vec2(0.5, 0.55)); frag = vec4(c * vig, 1.0); }"""

MESH_VS = """#version 330 core
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNormal;
uniform mat4 uMVP; uniform mat4 uModel; uniform mat3 uNormalMat; out vec3 vNormal; out vec3 vPos;
void main(){ vec4 w = uModel * vec4(aPos, 1.0); vNormal = uNormalMat * aNormal; vPos = w.xyz; gl_Position = uMVP * w; }"""
MESH_FS = """#version 330 core
in vec3 vNormal; in vec3 vPos; out vec4 frag;
uniform vec3 uColor; uniform vec3 uEye; uniform float uOverhang; uniform int uShowOverhang; uniform float uClipZ; uniform float uAlpha;
void main(){
  if (vPos.z > uClipZ) discard;
  vec3 n = normalize(vNormal);
  bool inside = !gl_FrontFacing;
  if (inside) n = -n;
  vec3 v = normalize(uEye - vPos);
  vec3 base = uColor;
  if (uShowOverhang == 1 && n.z < -uOverhang && vPos.z > 0.05) base = mix(base, vec3(1.0, 0.30, 0.36), 0.85);
  if (inside) base = vec3(0.95, 0.55, 0.25);
  vec3 l1 = normalize(vec3(0.45, -0.55, 0.75)); vec3 l2 = normalize(vec3(-0.6, 0.6, 0.35));
  float d = max(dot(n, l1), 0.0) * 0.72 + max(dot(n, l2), 0.0) * 0.28;
  float hemi = 0.5 + 0.5 * n.z;
  vec3 amb = mix(vec3(0.14, 0.15, 0.18), vec3(0.30, 0.32, 0.36), hemi);
  vec3 h = normalize(l1 + v); float spec = pow(max(dot(n, h), 0.0), 48.0) * 0.30;
  float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0) * 0.22;
  vec3 c = base * (amb + d) + vec3(spec) + rim * vec3(0.55, 0.72, 1.0);
  frag = vec4(c, uAlpha);
}"""

BED_VS = """#version 330 core
layout(location=0) in vec3 aPos; uniform mat4 uMVP; out vec2 vXY;
void main(){ vXY = aPos.xy; gl_Position = uMVP * vec4(aPos, 1.0); }"""
BED_FS = """#version 330 core
in vec2 vXY; out vec4 frag; uniform vec2 uBed; uniform vec3 uBase; uniform vec4 uPlate; uniform vec3 uGrid; uniform vec3 uAccent;
float grid(vec2 p, float s, float w){ vec2 c = abs(fract(p / s - 0.5) - 0.5) * s / max(fwidth(p), vec2(1e-4)); return 1.0 - clamp(min(c.x, c.y) - w, 0.0, 1.0); }
void main(){
  bool onPlate = vXY.x >= uPlate.x && vXY.y >= uPlate.y && vXY.x <= uPlate.z && vXY.y <= uPlate.w;
  vec3 c = onPlate ? uBase : mix(uBase, uGrid, 0.45);   // outside the chosen plate: shaded
  c = mix(c, uGrid, grid(vXY, 10.0, 0.0) * (onPlate ? 0.35 : 0.12));
  c = mix(c, uGrid, grid(vXY, 50.0, 0.4) * 0.8);
  vec2 e = min(vXY, uBed - vXY); float edge = min(e.x, e.y);
  c = mix(c, uGrid, 1.0 - smoothstep(0.0, max(fwidth(edge) * 2.0, 0.4), edge));
  vec2 pe = min(vXY - uPlate.xy, uPlate.zw - vXY); float pedge = abs(min(pe.x, pe.y));
  c = mix(c, uAccent, 1.0 - smoothstep(0.0, max(fwidth(pedge) * 2.0, 0.4), pedge));
  frag = vec4(c, 1.0);
}"""

LINE_VS = """#version 330 core
layout(location=0) in vec3 aPos; uniform mat4 uMVP;
void main(){ gl_Position = uMVP * vec4(aPos, 1.0); }"""
LINE_FS = """#version 330 core
out vec4 frag; uniform vec4 uColor; void main(){ frag = uColor; }"""

PATH_VS = """#version 330 core
layout(location=0) in vec3 aPos; layout(location=1) in float aKind; layout(location=2) in float aLayer;
out VS { float kind; float layer; } vs;
void main(){ gl_Position = vec4(aPos, 1.0); vs.kind = aKind; vs.layer = aLayer; }"""
PATH_GS = """#version 330 core
layout(lines) in; layout(triangle_strip, max_vertices = 4) out;
in VS { float kind; float layer; } gs[];
uniform mat4 uMVP; uniform vec3 uEye; uniform float uWidth; uniform float uMinLayer; uniform float uMaxLayer;
uniform int uMask; uniform vec3 uColors[8];
out vec3 fColor; out float fS; out float fTop;
void main(){
  float layer = gs[0].layer; int kind = int(gs[0].kind + 0.5);
  if (layer < uMinLayer - 0.5 || layer > uMaxLayer + 0.5) return;
  if ((uMask & (1 << kind)) == 0) return;
  vec3 a = gl_in[0].gl_Position.xyz; vec3 b = gl_in[1].gl_Position.xyz;
  vec3 dir = b - a; float len = length(dir); if (len < 1e-6) return; dir /= len;
  float w = uWidth * (kind == 7 ? 0.25 : 1.0);
  a -= dir * w * 0.35; b += dir * w * 0.35;
  vec3 view = normalize(uEye - (a + b) * 0.5);
  vec3 side = cross(dir, view);
  if (length(side) < 1e-5) side = vec3(-dir.y, dir.x, 0.0);
  side = normalize(side) * w * 0.5;
  fTop = layer > uMaxLayer - 0.5 ? 1.0 : 0.0;
  fColor = uColors[kind];
  fS = -1.0; gl_Position = uMVP * vec4(a - side, 1.0); EmitVertex();
  fS =  1.0; gl_Position = uMVP * vec4(a + side, 1.0); EmitVertex();
  fS = -1.0; gl_Position = uMVP * vec4(b - side, 1.0); EmitVertex();
  fS =  1.0; gl_Position = uMVP * vec4(b + side, 1.0); EmitVertex();
  EndPrimitive();
}"""
PATH_FS = """#version 330 core
in vec3 fColor; in float fS; in float fTop; out vec4 frag;
void main(){ float k = sqrt(max(0.0, 1.0 - fS * fS));
  vec3 c = fColor * (0.30 + 0.80 * k) + vec3(0.22) * pow(k, 14.0);
  if (fTop < 0.5) c *= 0.72; frag = vec4(c, 1.0); }"""


SEL_TOGGLE, SEL_ADD = 1, 2       # objectClicked flags: Ctrl toggles an object, Shift adds it


def _compile(vs: str, fs: str, gs: str | None = None) -> int:
    prog = GL.glCreateProgram()
    shaders = []
    for src, kind in ((vs, GL.GL_VERTEX_SHADER), (fs, GL.GL_FRAGMENT_SHADER), (gs, GL.GL_GEOMETRY_SHADER)):
        if src is None:
            continue
        sh = GL.glCreateShader(kind)
        GL.glShaderSource(sh, src)
        GL.glCompileShader(sh)
        if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(sh).decode())
        GL.glAttachShader(prog, sh)
        shaders.append(sh)
    GL.glLinkProgram(prog)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(prog).decode())
    for sh in shaders:
        GL.glDeleteShader(sh)
    return prog


class _Buffer:
    """A VAO + VBO holding interleaved float32 attributes."""

    def __init__(self, data: np.ndarray, layout: list[int]):
        data = np.ascontiguousarray(data, dtype=np.float32)
        self.count = len(data)
        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data if data.size else None, GL.GL_STATIC_DRAW)
        stride = sum(layout) * 4
        offset = 0
        for loc, size in enumerate(layout):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, size, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(offset))
            offset += size * 4
        GL.glBindVertexArray(0)

    def draw(self, mode):
        if self.count:
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(mode, 0, self.count)

    def delete(self):
        GL.glDeleteBuffers(1, [self.vbo])
        GL.glDeleteVertexArrays(1, [self.vao])


def mesh_vertices(mesh: Mesh) -> np.ndarray:
    """Flat-shaded (pos, normal) vertex array."""
    tris = mesh.triangles.astype(np.float32)
    normals = np.repeat(mesh.face_normals().astype(np.float32)[:, None, :], 3, axis=1)
    return np.concatenate([tris, normals], axis=2).reshape(-1, 6)


def _torus(centre, axis: int, radius: float, tube: float, segments: int = 96, sides: int = 8) -> Mesh:
    """Ring around ``centre`` in the plane perpendicular to world axis ``axis``."""
    u, v, n = np.eye(3)[(axis + 1) % 3], np.eye(3)[(axis + 2) % 3], np.eye(3)[axis]
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    b = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring = np.cos(a)[:, None] * u + np.sin(a)[:, None] * v                 # (segments, 3)
    verts = (np.asarray(centre)[None, None, :] + ring[:, None, :] * (radius + tube * np.cos(b))[None, :, None]
             + n[None, None, :] * (tube * np.sin(b))[None, :, None]).reshape(-1, 3)
    i, j = np.meshgrid(np.arange(segments), np.arange(sides), indexing="ij")
    i2, j2 = (i + 1) % segments, (j + 1) % sides
    a0, a1, b0, b1 = i * sides + j, i2 * sides + j, i * sides + j2, i2 * sides + j2
    faces = np.concatenate([np.stack([a0, a1, b1], -1).reshape(-1, 3), np.stack([a0, b1, b0], -1).reshape(-1, 3)])
    return Mesh(verts, faces, "ring")


# ------------------------------------------------------------------ camera
def _perspective(fov_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    m = np.zeros((4, 4), dtype=np.float64)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def _look_at(eye, target, up):
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-6:
        s = np.cross(f, np.array([0.0, 1.0, 0.0]))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


class Camera:
    def __init__(self):
        self.target = np.array([110.0, 110.0, 20.0])
        self.distance = 420.0
        self.yaw = -60.0
        self.pitch = 32.0
        self.fov = 35.0

    def eye(self) -> np.ndarray:
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        return self.target + self.distance * np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])

    def view(self) -> np.ndarray:
        up = np.array([0.0, 0.0, 1.0]) if abs(self.pitch) < 89.5 else np.array([0.0, 1.0, 0.0])
        return _look_at(self.eye(), self.target.astype(np.float64), up)

    def projection(self, aspect: float) -> np.ndarray:
        return _perspective(self.fov, aspect, max(0.5, self.distance * 0.01), self.distance * 20 + 2000)


# ------------------------------------------------------------------ widget
class Viewer(QOpenGLWidget):
    objectClicked = Signal(object, int)       # uid or None, SEL_TOGGLE (Ctrl) / SEL_ADD (Shift) flags
    boxSelected = Signal(object, bool)        # uids inside a Shift/Ctrl-dragged rectangle, additive
    contextRequested = Signal(object, object)  # uid under a right click (or None), global position
    objectDragged = Signal(object, float, float)
    objectDragFinished = Signal(object)
    glFailed = Signal(str)
    surfacePicked = Signal(object, object)     # uid, world point (support "add" mode)
    previewObjectClicked = Signal(object)      # a model was clicked while previewing
    rotateStarted = Signal(object)             # uid
    rotateDragged = Signal(object, int, float) # uid, axis (0 x, 1 y, 2 z), degrees since the drag started
    rotateFinished = Signal(object)
    facePicked = Signal(object, object)        # uid, world normal of the clicked face
    holePicked = Signal(object, object, object)  # uid, world point, world normal (drain hole tool)
    measured = Signal(object, object)            # first and second world point
    rayPicked = Signal(object, object)         # origin, direction (support "remove" mode)
    cameraChanged = Signal()                   # after every repaint (keeps the view cube in step)
    hoverRay = Signal(object, object, object, object)  # origin, direction, uid, model hit point (support modes)

    MODE_PREPARE, MODE_PREVIEW = "prepare", "preview"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.StrongFocus)
        self.camera = Camera()
        self.bed = (220.0, 220.0, 250.0)
        self.technology = "fdm"
        self.mode = self.MODE_PREPARE
        self.pick_mode: str | None = None        # None | "add" (one support per click) | "remove" (one per click)
        self.marker = None                       # where a support would be added (add mode hover)
        self.highlight = None                    # (mesh in bed coordinates, buffer) of the support under the cursor
        self.tool = "move"                       # move | rotate | face | hole | measure
        self.measure_points: list = []
        self._rot = None                         # active ring drag: (uid, axis, start angle, last degrees)
        self._hover_axis = None
        self._gizmo_cache = None
        self.setMouseTracking(True)
        self.objects: dict = {}          # uid -> dict(mesh, buffer, color, dirty)
        self.selected = None             # primary selection (gizmo, transform panel)
        self.selection: set = set()      # every selected uid
        self._band = None                # (origin, QRubberBand) while box-selecting
        self._click_select = None        # uid to select alone on release (click inside a group)
        self.extra: list = []            # [(mesh, color)] e.g. SLA supports
        self._extra_buffers: list = []
        self._extra_dirty = False
        self.paths: np.ndarray | None = None
        self._paths_buffer = None
        self._paths_dirty = False
        self.path_width = 0.45
        self.layer_min, self.layer_max = 0, 0
        self.feature_mask = 0xFF & ~(1 << 7)
        self.clip_z = 1e9
        self.show_overhang = False
        self.overhang_sin = math.sin(math.radians(50))
        self.show_models_in_preview = False
        self._bed_dirty = True
        self._ready = False
        self._drag = None
        self._last = QPointF()
        self._picked = False
        self._press_pos = QPointF()
        self.last_click = QPointF()

    @staticmethod
    def configure_default_format():
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        # Qt's software renderer (Mesa) may only offer 3.3+ as a compatibility context
        software = bool(os.environ.get("UNISLICER_SOFTWARE_GL"))
        fmt.setProfile(QSurfaceFormat.CompatibilityProfile if software else QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(0 if software else 4)  # multisampling is costly on the CPU renderer
        QSurfaceFormat.setDefaultFormat(fmt)

    # ---------------------------------------------------------- public API
    def set_bed(self, x: float, y: float, z: float, technology: str, plate: tuple | None = None):
        self.bed = (float(x), float(y), float(z))
        self.plate = tuple(float(v) for v in plate) if plate else (0.0, 0.0, float(x), float(y))
        self.technology = technology
        self._bed_dirty = True
        self.update()

    def set_object(self, uid, mesh: Mesh, matrix: np.ndarray | None = None, state: str = "normal"):
        """Show ``mesh`` (in its own coordinates) placed by the 4x4 ``matrix``.

        The mesh is uploaded to the GPU once; later calls with the same mesh only update the matrix,
        so moving, rotating and scaling stay instant even for very large models."""
        matrix = np.eye(4) if matrix is None else np.asarray(matrix, dtype=np.float64)
        old = self.objects.get(uid)
        if old is not None and old["mesh"] is mesh:
            old["matrix"], old["state"] = matrix, state
        else:
            if old and old.get("buffer"):
                self._trash(old["buffer"])
            self.objects[uid] = {"mesh": mesh, "buffer": None, "state": state, "matrix": matrix}
        self.update()

    def set_object_transform(self, uid, matrix: np.ndarray):
        if uid in self.objects:
            self.objects[uid]["matrix"] = np.asarray(matrix, dtype=np.float64)
            self.update()

    def set_object_state(self, uid, state: str):
        if uid in self.objects:
            self.objects[uid]["state"] = state
            self.update()

    def remove_object(self, uid):
        obj = self.objects.pop(uid, None)
        if obj:
            for key in ("buffer", "sup_buffer", "hole_buffer"):
                if obj.get(key):
                    self._trash(obj[key])
        self.update()

    def set_object_holes(self, uid, mesh: Mesh | None):
        """Drain-hole markers in the object's own coordinates."""
        obj = self.objects.get(uid)
        if obj is None or (obj.get("holes") is mesh and obj.get("hole_buffer") is not None):
            return                        # unchanged: keep what is already on the GPU
        if obj.get("hole_buffer"):
            self._trash(obj["hole_buffer"])
        obj["holes"], obj["hole_buffer"] = mesh, None
        self.update()

    def set_object_supports(self, uid, mesh: Mesh | None):
        """Support structure in the object's own coordinates (drawn with the object's matrix)."""
        obj = self.objects.get(uid)
        if obj is None or (obj.get("supports") is mesh and obj.get("sup_buffer") is not None):
            return                        # unchanged: keep what is already on the GPU
        if obj.get("sup_buffer"):
            self._trash(obj["sup_buffer"])
        obj["supports"], obj["sup_buffer"] = mesh, None
        self.update()

    def set_selected(self, uid, others=None):
        self.selected = uid
        self.selection = set(others or ()) | ({uid} if uid is not None else set())
        self.update()

    def set_highlight(self, mesh):
        """Draw ``mesh`` (bed coordinates) in red over the scene, e.g. the support a click would remove."""
        if self.highlight is not None and self.highlight[1] is not None:
            self._trash(self.highlight[1])
        self.highlight = None if mesh is None or len(mesh.faces) == 0 else (mesh, None)
        self.update()

    def set_marker(self, point):
        """Mark where a support would be added (None hides it)."""
        same = (point is None and self.marker is None) or (
            point is not None and self.marker is not None and np.allclose(point, self.marker))
        if not same:
            self.marker = None if point is None else np.asarray(point, dtype=float)
            self.update()

    def set_extra_meshes(self, items):
        self.extra = list(items)
        self._extra_dirty = True
        self.update()

    def set_toolpaths(self, vertices: np.ndarray | None, width: float):
        self.paths = vertices
        self.path_width = width
        self._paths_dirty = True
        self.update()

    def set_layer_range(self, lo: int, hi: int, clip_z: float | None = None):
        self.layer_min, self.layer_max = lo, hi
        self.clip_z = 1e9 if clip_z is None else clip_z
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def set_feature_visible(self, index: int, visible: bool):
        if visible:
            self.feature_mask |= 1 << index
        else:
            self.feature_mask &= ~(1 << index)
        self.update()

    def set_overhang(self, show: bool, angle_deg: float):
        self.show_overhang = show
        self.overhang_sin = math.sin(math.radians(angle_deg))
        self.update()

    # ---------------------------------------------------------- camera views
    def _scene_center(self):
        x0, y0, x1, y1 = getattr(self, "plate", (0.0, 0.0, self.bed[0], self.bed[1]))
        return np.array([(x0 + x1) / 2, (y0 + y1) / 2, 0.0])

    def view_preset(self, name: str):
        c = self.camera
        bx, by, bz = self.bed
        c.target = self._scene_center() + np.array([0, 0, min(bz, 60) * 0.3])
        c.distance = max(bx, by, bz * 0.8) * 2.05 + 30
        c.yaw, c.pitch = {"iso": (-62.0, 30.0), "top": (-90.0, 89.9), "front": (-90.0, 6.0), "side": (0.0, 6.0)}[name]
        self.update()

    @staticmethod
    def _world_box(obj) -> np.ndarray:
        b = obj["mesh"].bounds
        corners = np.array([[x, y, z] for x in b[:, 0] for y in b[:, 1] for z in b[:, 2]])
        w = corners @ obj["matrix"][:3, :3].T + obj["matrix"][:3, 3]
        return np.array([w.min(axis=0), w.max(axis=0)])

    def fit_objects(self):
        if not self.objects:
            self.view_preset("iso")
            return
        boxes = [self._world_box(o) for o in self.objects.values()]
        lo = np.min([b[0] for b in boxes], axis=0)
        hi = np.max([b[1] for b in boxes], axis=0)
        self.camera.target = (lo + hi) / 2
        radius = np.linalg.norm(hi - lo) / 2
        self.camera.distance = max(30.0, radius / math.sin(math.radians(self.camera.fov / 2)) * 1.15)
        self.update()

    # ---------------------------------------------------------- GL lifecycle
    def initializeGL(self):
        try:
            fmt = self.context().format()
            if (fmt.majorVersion(), fmt.minorVersion()) < (3, 3):
                renderer = GL.glGetString(GL.GL_RENDERER)
                renderer = renderer.decode(errors="replace") if renderer else "unknown renderer"
                raise RuntimeError(f"OpenGL {fmt.majorVersion()}.{fmt.minorVersion()} ({renderer}); 3.3 is required")
            self._bg = _compile(BG_VS, BG_FS)
            self._mesh_prog = _compile(MESH_VS, MESH_FS)
            self._bed_prog = _compile(BED_VS, BED_FS)
            self._line_prog = _compile(LINE_VS, LINE_FS)
            self._path_prog = _compile(PATH_VS, PATH_FS, PATH_GS)
            self._empty_vao = GL.glGenVertexArrays(1)
            self._bed_buf = None
            self._box_buf = None
            self._trash_list = []
            self._ready = True
            print("OpenGL:", GL.glGetString(GL.GL_VERSION), GL.glGetString(GL.GL_RENDERER), file=sys.stderr, flush=True)
        except Exception as exc:  # pragma: no cover - depends on the user's GPU
            print("OpenGL initialisation failed:", exc, file=sys.stderr, flush=True)
            self._ready = False
            self.glFailed.emit(str(exc))

    def _trash(self, buf):
        if hasattr(self, "_trash_list"):
            self._trash_list.append(buf)

    def _u(self, prog, name):
        return GL.glGetUniformLocation(prog, name)

    def _upload_dirty(self):
        for buf in self._trash_list:
            buf.delete()
        self._trash_list.clear()
        if self._bed_dirty:
            for b in (self._bed_buf, self._box_buf):
                if b:
                    b.delete()
            bx, by, bz = self.bed
            z = -0.02
            quad = np.array([[0, 0, z], [bx, 0, z], [bx, by, z], [0, 0, z], [bx, by, z], [0, by, z]], dtype=np.float32)
            self._bed_buf = _Buffer(quad, [3])
            c = np.array([[x, y, zz] for x in (0, bx) for y in (0, by) for zz in (0, bz)], dtype=np.float32)
            edges = [(0, 1), (2, 3), (4, 5), (6, 7), (1, 3), (3, 7), (7, 5), (5, 1)]
            self._box_buf = _Buffer(np.array([c[i] for e in edges for i in e]), [3])
            self._bed_dirty = False
        for obj in self.objects.values():
            if obj["buffer"] is None:
                obj["buffer"] = _Buffer(mesh_vertices(obj["mesh"]), [3, 3])
            if obj.get("supports") is not None and obj.get("sup_buffer") is None and len(obj["supports"].faces):
                obj["sup_buffer"] = _Buffer(mesh_vertices(obj["supports"]), [3, 3])
            if obj.get("holes") is not None and obj.get("hole_buffer") is None and len(obj["holes"].faces):
                obj["hole_buffer"] = _Buffer(mesh_vertices(obj["holes"]), [3, 3])
        if self._extra_dirty:
            for b, _ in self._extra_buffers:
                b.delete()
            self._extra_buffers = [(_Buffer(mesh_vertices(m), [3, 3]), col) for m, col in self.extra if len(m.faces)]
            self._extra_dirty = False
        if self._paths_dirty:
            if self._paths_buffer:
                self._paths_buffer.delete()
            self._paths_buffer = _Buffer(self.paths, [3, 1, 1]) if self.paths is not None and len(self.paths) else None
            self._paths_dirty = False

    def paintGL(self):
        if not self._ready:
            return  # no usable OpenGL: the main window shows an explanation instead
        try:
            self._paint()
            self.cameraChanged.emit()
        except Exception as exc:  # pragma: no cover - depends on the user's GPU
            self._ready = False
            print("OpenGL rendering failed:", exc, file=sys.stderr, flush=True)
            self.glFailed.emit(str(exc))

    def _paint(self):
        self._upload_dirty()
        w, h = max(1, self.width()), max(1, self.height())
        GL.glViewport(0, 0, int(w * self.devicePixelRatioF()), int(h * self.devicePixelRatioF()))
        GL.glClearColor(0, 0, 0, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

        # gradient background
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glUseProgram(self._bg)
        GL.glUniform3f(self._u(self._bg, "uTop"), *theme.VIEW_BG_TOP)
        GL.glUniform3f(self._u(self._bg, "uBottom"), *theme.VIEW_BG_BOTTOM)
        GL.glBindVertexArray(self._empty_vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LEQUAL)
        GL.glEnable(GL.GL_MULTISAMPLE)

        mvp = (self.camera.projection(w / h) @ self.camera.view()).astype(np.float32)
        eye = self.camera.eye().astype(np.float32)

        # bed
        GL.glUseProgram(self._bed_prog)
        GL.glUniformMatrix4fv(self._u(self._bed_prog, "uMVP"), 1, GL.GL_TRUE, mvp)
        GL.glUniform2f(self._u(self._bed_prog, "uBed"), self.bed[0], self.bed[1])
        GL.glUniform4f(self._u(self._bed_prog, "uPlate"), *getattr(self, "plate", (0.0, 0.0, self.bed[0], self.bed[1])))
        GL.glUniform3f(self._u(self._bed_prog, "uBase"), *theme.BED_COLOR)
        GL.glUniform3f(self._u(self._bed_prog, "uGrid"), *theme.GRID_COLOR)
        accent = QColor(theme.ACCENT)
        GL.glUniform3f(self._u(self._bed_prog, "uAccent"), accent.redF(), accent.greenF(), accent.blueF())
        GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glPolygonOffset(1.0, 1.0)
        self._bed_buf.draw(GL.GL_TRIANGLES)
        GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)

        # build volume
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glUseProgram(self._line_prog)
        GL.glUniformMatrix4fv(self._u(self._line_prog, "uMVP"), 1, GL.GL_TRUE, mvp)
        GL.glUniform4f(self._u(self._line_prog, "uColor"), 0.45, 0.55, 0.75, 0.35)
        self._box_buf.draw(GL.GL_LINES)
        GL.glDisable(GL.GL_BLEND)

        preview = self.mode == self.MODE_PREVIEW
        show_models = not preview or self.technology == "sla" or self._paths_buffer is None or self.show_models_in_preview
        clip = self.clip_z if preview else 1e9
        p = self._mesh_prog
        GL.glUseProgram(p)
        GL.glUniformMatrix4fv(self._u(p, "uMVP"), 1, GL.GL_TRUE, mvp)
        GL.glUniform3f(self._u(p, "uEye"), *eye)
        GL.glUniform1f(self._u(p, "uOverhang"), self.overhang_sin)
        GL.glUniform1f(self._u(p, "uClipZ"), clip)
        GL.glUniform1f(self._u(p, "uAlpha"), 1.0)
        if show_models:
            GL.glUniform1i(self._u(p, "uShowOverhang"), int(self.show_overhang and not preview))
            for uid, obj in self.objects.items():
                if preview and self.technology == "sla":
                    continue  # the SLA preview shows the elevated copies passed as extras
                state = obj["state"]
                if state == "cutter":
                    continue  # drawn translucent after everything opaque
                color = theme.MODEL_OUTSIDE if state == "outside" else (
                    theme.MODEL_SELECTED if uid in self.selection else theme.MODEL_COLOR)
                GL.glUniform3f(self._u(p, "uColor"), *color)
                self._set_model(p, obj["matrix"])
                obj["buffer"].draw(GL.GL_TRIANGLES)
                if obj.get("hole_buffer") is not None and not preview:
                    GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
                    GL.glUniform3f(self._u(p, "uColor"), 1.0, 0.35, 0.42)
                    obj["hole_buffer"].draw(GL.GL_TRIANGLES)
                    GL.glUniform1i(self._u(p, "uShowOverhang"), int(self.show_overhang and not preview))
                if obj.get("sup_buffer") is not None and not preview and self.technology == "sla":
                    GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
                    GL.glUniform3f(self._u(p, "uColor"), *theme.SUPPORT_COLOR)
                    obj["sup_buffer"].draw(GL.GL_TRIANGLES)
                    GL.glUniform1i(self._u(p, "uShowOverhang"), int(self.show_overhang and not preview))
        GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
        cutters = [(uid, o) for uid, o in self.objects.items() if o["state"] == "cutter"] if not preview else []
        if cutters:
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glDepthMask(GL.GL_FALSE)
            GL.glUniform1f(self._u(p, "uAlpha"), 0.45)
            for uid, obj in cutters:
                color = (1.0, 0.55, 0.30) if uid in self.selection else (1.0, 0.36, 0.42)
                GL.glUniform3f(self._u(p, "uColor"), *color)
                self._set_model(p, obj["matrix"])
                obj["buffer"].draw(GL.GL_TRIANGLES)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glDisable(GL.GL_BLEND)
            GL.glUniform1f(self._u(p, "uAlpha"), 1.0)
        self._set_model(p, np.eye(4))
        if preview or self.technology == "sla":
            for buf, color in self._extra_buffers:
                GL.glUniform3f(self._u(p, "uColor"), *color)
                buf.draw(GL.GL_TRIANGLES)

        if not preview:
            for uid in self.selection:
                if uid in self.objects:
                    self._draw_selection_box(mvp, uid, primary=uid == self.selected)
        if self.measure_points:
            pts = np.array(self.measure_points, dtype=np.float32)
            if len(pts) == 1:
                pts = np.vstack([pts, pts + [0, 0, 0.01]])
            GL.glDisable(GL.GL_DEPTH_TEST)
            buf = _Buffer(pts[:2], [3])
            GL.glUseProgram(self._line_prog)
            GL.glUniformMatrix4fv(self._u(self._line_prog, "uMVP"), 1, GL.GL_TRUE, mvp)
            GL.glUniform4f(self._u(self._line_prog, "uColor"), 1.0, 0.85, 0.25, 1.0)
            buf.draw(GL.GL_LINES)
            buf.delete()
            GL.glEnable(GL.GL_DEPTH_TEST)
        if self.highlight is not None and not preview:
            mesh, buf = self.highlight
            if buf is None:
                buf = _Buffer(mesh_vertices(mesh), [3, 3])
                self.highlight = (mesh, buf)
            GL.glUseProgram(p)
            GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
            GL.glUniform1f(self._u(p, "uClipZ"), 1e9)
            GL.glUniform3f(self._u(p, "uColor"), 1.0, 0.25, 0.32)
            self._set_model(p, np.eye(4))
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glPolygonOffset(-1.0, -4.0)
            buf.draw(GL.GL_TRIANGLES)
            GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        if self.marker is not None and not preview:
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glUseProgram(p)
            GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
            GL.glUniform1f(self._u(p, "uClipZ"), 1e9)
            GL.glUniform3f(self._u(p, "uColor"), 0.2, 0.85, 0.5)
            self._set_model(p, np.eye(4))
            from ..primitives import sphere
            ball = _Buffer(mesh_vertices(sphere(0.6, 8, 12).translated(self.marker - [0, 0, 0.6])), [3, 3])
            ball.draw(GL.GL_TRIANGLES)
            ball.delete()
            GL.glEnable(GL.GL_DEPTH_TEST)
        rings = self._gizmo_meshes()
        if rings:
            GL.glClear(GL.GL_DEPTH_BUFFER_BIT)   # rings stay visible through the model but hide their own back
            GL.glUseProgram(p)
            GL.glUniform1f(self._u(p, "uClipZ"), 1e9)
            GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
            self._set_model(p, np.eye(4))
            active = self._rot[1] if self._rot else self._hover_axis
            for k, buf in enumerate(rings):
                base = ((1.0, 0.35, 0.42), (0.30, 0.85, 0.55), (0.35, 0.60, 1.0))[k]
                color = tuple(min(1.0, c * 1.35 + 0.15) for c in base) if k == active else base
                GL.glUniform3f(self._u(p, "uColor"), *color)
                buf.draw(GL.GL_TRIANGLES)

        if preview and self._paths_buffer is not None:
            q = self._path_prog
            GL.glUseProgram(q)
            GL.glUniformMatrix4fv(self._u(q, "uMVP"), 1, GL.GL_TRUE, mvp)
            GL.glUniform3f(self._u(q, "uEye"), *eye)
            GL.glUniform1f(self._u(q, "uWidth"), self.path_width)
            GL.glUniform1f(self._u(q, "uMinLayer"), float(self.layer_min))
            GL.glUniform1f(self._u(q, "uMaxLayer"), float(self.layer_max))
            GL.glUniform1i(self._u(q, "uMask"), int(self.feature_mask))
            colors = np.array([theme.feature_rgb(k) for k in theme.FEATURE_ORDER], dtype=np.float32)
            GL.glUniform3fv(self._u(q, "uColors"), len(colors), colors)
            self._paths_buffer.draw(GL.GL_LINES)
        GL.glBindVertexArray(0)

    def _draw_selection_box(self, mvp, uid, primary: bool = True):
        box = self._world_box(self.objects[uid])
        pad = 0.6
        lo, hi = box[0] - pad, box[1] + pad
        c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], dtype=np.float32)
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        buf = _Buffer(np.array([c[i] for e in edges for i in e]), [3])
        GL.glUseProgram(self._line_prog)
        GL.glUniformMatrix4fv(self._u(self._line_prog, "uMVP"), 1, GL.GL_TRUE, mvp)
        GL.glUniform4f(self._u(self._line_prog, "uColor"), 1.0, 0.64, 0.28, 0.9 if primary else 0.45)
        buf.draw(GL.GL_LINES)
        buf.delete()

    def _set_model(self, prog, matrix: np.ndarray):
        lin = matrix[:3, :3]
        det = np.linalg.det(lin)
        normal = np.linalg.inv(lin).T if abs(det) > 1e-12 else np.eye(3)
        GL.glUniformMatrix4fv(self._u(prog, "uModel"), 1, GL.GL_TRUE, matrix.astype(np.float32))
        GL.glUniformMatrix3fv(self._u(prog, "uNormalMat"), 1, GL.GL_TRUE, normal.astype(np.float32))
        GL.glFrontFace(GL.GL_CW if det < 0 else GL.GL_CCW)  # mirroring flips the winding

    # ---------------------------------------------------------- picking
    def _ray(self, pos: QPointF):
        w, h = max(1, self.width()), max(1, self.height())
        x = 2.0 * pos.x() / w - 1.0
        y = 1.0 - 2.0 * pos.y() / h
        inv = np.linalg.inv(self.camera.projection(w / h) @ self.camera.view())
        near = inv @ np.array([x, y, -1.0, 1.0])
        far = inv @ np.array([x, y, 1.0, 1.0])
        near, far = near[:3] / near[3], far[:3] / far[3]
        d = far - near
        return near, d / np.linalg.norm(d)

    @staticmethod
    def _ray_mesh(origin, direction, mesh: Mesh):
        t = mesh.triangles
        lo, hi = mesh.bounds
        # slab test against the bounding box first
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / direction
            t0, t1 = (lo - origin) * inv, (hi - origin) * inv
        tmin = np.nanmax(np.minimum(t0, t1))
        tmax = np.nanmin(np.maximum(t0, t1))
        if tmax < max(tmin, 0):
            return None
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        p = np.cross(direction, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-12
        inv_det = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0)
        s = origin - t[:, 0]
        u = np.einsum("ij,ij->i", s, p) * inv_det
        q = np.cross(s, e1)
        v = (q @ direction) * inv_det
        dist = np.einsum("ij,ij->i", e2, q) * inv_det
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (dist > 0)
        if not hit.any():
            return None
        idx = np.flatnonzero(hit)
        k = idx[np.argmin(dist[idx])]
        return float(dist[k]), int(k)

    def pick(self, pos: QPointF):
        """The object under the cursor: its surface, its supports, or failing that its bounding box
        (so small or thin parts are easy to grab)."""
        uid, _ = self.pick_hit(pos, supports=True)
        if uid is not None:
            return uid
        origin, direction = self._ray(pos)
        best, best_t = None, np.inf
        for k, obj in self.objects.items():
            lo, hi = self._world_box(obj)
            lo, hi = lo - 0.5, hi + 0.5
            with np.errstate(divide="ignore", invalid="ignore"):
                inv = 1.0 / direction
                t0, t1 = (lo - origin) * inv, (hi - origin) * inv
            tmin = np.nanmax(np.minimum(t0, t1))
            tmax = np.nanmin(np.maximum(t0, t1))
            if tmax >= max(tmin, 0) and tmin < best_t:
                best, best_t = k, tmin
        return best

    def pick_hit(self, pos: QPointF, supports: bool = False):
        """(uid, world hit point) of the object under the cursor, or (None, None).
        With ``supports`` its supports count as part of it."""
        origin, direction = self._ray(pos)
        best, best_d = None, np.inf
        for uid, obj in self.objects.items():
            # intersect in the object's own coordinates; the ray parameter stays comparable
            inv = np.linalg.inv(obj["matrix"])
            o_local = inv[:3, :3] @ origin + inv[:3, 3]
            d_local = inv[:3, :3] @ direction
            r = self._ray_mesh(o_local, d_local, obj["mesh"])
            if r is not None and r[0] < best_d:
                best, best_d, self._last_face = uid, r[0], r[1]
            if supports and obj.get("supports") is not None and self.technology == "sla":
                r = self._ray_mesh(o_local, d_local, obj["supports"])
                if r is not None and r[0] < best_d:
                    best, best_d = uid, r[0]
        return (best, origin + direction * best_d) if best is not None else (None, None)

    def pick_face(self, pos: QPointF):
        """(uid, world-space normal) of the face under the cursor, or (None, None)."""
        uid, _ = self.pick_hit(pos)
        if uid is None:
            return None, None
        obj = self.objects[uid]
        mesh = obj["mesh"]
        tri = mesh.triangles[self._last_face]
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        lin = obj["matrix"][:3, :3]
        n = np.linalg.inv(lin).T @ n
        return uid, n / (np.linalg.norm(n) or 1.0)

    # ---------------------------------------------------------- rotate gizmo
    def _gizmo(self):
        """(centre, radius) of the rotation rings around the selected model, or None."""
        if self.tool != "rotate" or self.mode != self.MODE_PREPARE or self.selected not in self.objects:
            return None
        box = self._world_box(self.objects[self.selected])
        centre = (box[0] + box[1]) / 2
        return centre, float(np.linalg.norm(box[1] - box[0])) * 0.55 + 2.0

    def _ring_hit(self, pos: QPointF, only_axis: int | None = None):
        """(axis, angle, ray t) where the cursor ray meets a rotation ring, or None."""
        g = self._gizmo()
        if g is None:
            return None
        c, radius = g
        origin, d = self._ray(pos)
        best = None
        for k in ([only_axis] if only_axis is not None else (0, 1, 2)):
            n = np.eye(3)[k]
            denom = float(d @ n)
            if abs(denom) < 1e-6:
                continue
            t = float((c - origin) @ n) / denom
            if t < 0:
                continue
            rel = origin + d * t - c
            u, v = np.eye(3)[(k + 1) % 3], np.eye(3)[(k + 2) % 3]
            ang = math.degrees(math.atan2(rel @ v, rel @ u))
            if only_axis is None and abs(np.linalg.norm(rel) - radius) > radius * 0.12:
                continue
            if best is None or t < best[2]:
                best = (k, ang, t)
        return best

    def _gizmo_meshes(self):
        g = self._gizmo()
        if g is None:
            return None
        key = (tuple(np.round(g[0], 3)), round(g[1], 3))
        if self._gizmo_cache is None or self._gizmo_cache[0] != key:
            for b in (self._gizmo_cache[1] if self._gizmo_cache else []):
                self._trash(b)
            bufs = [_Buffer(mesh_vertices(_torus(g[0], k, g[1], max(0.25, g[1] * 0.022))), [3, 3]) for k in range(3)]
            self._gizmo_cache = (key, bufs)
        return self._gizmo_cache[1]

    def _bed_point(self, pos: QPointF, z: float = 0.0):
        origin, direction = self._ray(pos)
        if abs(direction[2]) < 1e-6:
            return None
        t = (z - origin[2]) / direction[2]
        return None if t < 0 else origin + direction * t

    # ---------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        self.last_click = e.position()            # where the floating edit bar opens
        self._last = e.position()
        self._press_pos = e.position()
        self._drag = None
        self._picked = False
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE and self.pick_mode:
            if self.pick_mode == "add":
                uid, point = self.pick_hit(e.position(), supports=False)
                if uid is not None:
                    self.surfacePicked.emit(uid, point)
                    self._picked = True
            else:
                origin, direction = self._ray(e.position())
                self.rayPicked.emit(origin, direction)
                self._picked = True
            return
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE and self.tool == "rotate":
            hit = self._ring_hit(e.position())
            if hit is not None:
                self._rot = (self.selected, hit[0], hit[1], 0.0)
                self.rotateStarted.emit(self.selected)
                self.update()
                return
        if e.button() == Qt.LeftButton and self.tool == "measure":
            uid, point = self.pick_hit(e.position())
            if point is None:
                point = self._bed_point(e.position())
            if point is not None:
                if len(self.measure_points) != 1:
                    self.measure_points = [point]
                else:
                    self.measure_points.append(point)
                    self.measured.emit(self.measure_points[0], self.measure_points[1])
                self._picked = True
                self.update()
            return
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE and self.tool == "hole":
            uid, point = self.pick_hit(e.position())
            if uid is not None:
                _, normal = self.pick_face(e.position())
                self.holePicked.emit(uid, point, normal)
                self._picked = True
            return
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE and self.tool == "face":
            uid, normal = self.pick_face(e.position())
            if uid is not None:
                self.objectClicked.emit(uid, 0)
                self.facePicked.emit(uid, normal)
                self._picked = True
            return
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE:
            uid = self.pick(e.position())
            mods = (SEL_TOGGLE if e.modifiers() & Qt.ControlModifier else 0) | (SEL_ADD if e.modifiers() & Qt.ShiftModifier else 0)
            if uid is not None and self.tool == "rotate":
                self.objectClicked.emit(uid, mods)   # select only; rotating happens on the rings
                self._picked = True
                return
            if uid is not None:
                if mods or uid not in self.selection:
                    self.objectClicked.emit(uid, mods)
                else:
                    self._click_select = uid       # a plain click (no drag) inside a group selects it alone
                if mods and uid not in self.selection:
                    return                         # Ctrl/Shift-click removed it from the selection: nothing to drag
                start = self._bed_point(e.position())
                if start is not None:
                    self._drag = (uid, start)
            elif mods:
                from PySide6.QtWidgets import QRubberBand
                band = QRubberBand(QRubberBand.Rectangle, self)
                band.setGeometry(int(e.position().x()), int(e.position().y()), 1, 1)
                band.show()
                self._band = (e.position(), band)

    def mouseMoveEvent(self, e):
        pos = e.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        if self._rot is not None and e.buttons() & Qt.LeftButton:
            uid, axis, start, _ = self._rot
            hit = self._ring_hit(pos, only_axis=axis)
            if hit is not None:
                delta = (hit[1] - start + 180.0) % 360.0 - 180.0
                step = 15.0 if e.modifiers() & Qt.ShiftModifier else 1.0
                delta = round(delta / step) * step
                if delta != self._rot[3]:
                    self._rot = (uid, axis, start, delta)
                    self.rotateDragged.emit(uid, axis, float(delta))
            return
        if self.pick_mode and not e.buttons():
            uid, point = self.pick_hit(pos, supports=False) if self.pick_mode == "add" else (None, None)
            self.hoverRay.emit(*self._ray(pos), uid, point)
            return
        if self.tool == "rotate" and not e.buttons():
            hit = self._ring_hit(pos)
            axis = hit[0] if hit else None
            if axis != self._hover_axis:
                self._hover_axis = axis
                self.setCursor(Qt.OpenHandCursor if axis is not None else Qt.ArrowCursor)
                self.update()
            return
        if self._band is not None:
            from PySide6.QtCore import QRect
            origin = self._band[0]
            self._band[1].setGeometry(QRect(origin.toPoint(), pos.toPoint()).normalized())
            return
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            uid, start = self._drag
            p = self._bed_point(pos)
            if p is not None:
                self.objectDragged.emit(uid, float(p[0] - start[0]), float(p[1] - start[1]))
                self._drag = (uid, p)
            return
        if e.buttons() & Qt.LeftButton:
            self.camera.yaw -= dx * 0.4
            self.camera.pitch = float(np.clip(self.camera.pitch + dy * 0.4, -89.9, 89.9))
            self.update()
        elif e.buttons() & (Qt.RightButton | Qt.MiddleButton):
            view = self.camera.view()
            right, up = view[0, :3], view[1, :3]
            scale = self.camera.distance * 0.0016
            self.camera.target -= right * dx * scale - up * dy * scale
            self.update()

    def objects_in_rect(self, rect) -> list:
        """uids whose bounding-box centre projects inside the widget rectangle ``rect``."""
        mvp = self.camera.projection(self.width() / max(1, self.height())) @ self.camera.view()
        out = []
        for uid, obj in self.objects.items():
            box = self._world_box(obj)
            c = mvp @ np.append((box[0] + box[1]) / 2, 1.0)
            if c[3] <= 0:
                continue
            x = (c[0] / c[3] * 0.5 + 0.5) * self.width()
            y = (0.5 - c[1] / c[3] * 0.5) * self.height()
            if rect.contains(int(x), int(y)):
                out.append(uid)
        return out

    def mouseReleaseEvent(self, e):
        moved = (e.position() - self._press_pos).manhattanLength() > 3
        click_select, self._click_select = self._click_select, None
        if self._band is not None:
            band = self._band[1]
            rect = band.geometry()
            band.hide()
            band.deleteLater()
            self._band = None
            self.boxSelected.emit(self.objects_in_rect(rect), True)
            return
        if e.button() == Qt.RightButton and not moved:
            uid = self.pick(e.position()) if self.mode == self.MODE_PREPARE else None
            self.contextRequested.emit(uid, e.globalPosition().toPoint())
            return
        if self._rot is not None:
            uid = self._rot[0]
            self._rot = None
            self.rotateFinished.emit(uid)
            self.update()
            return
        if self._picked:
            return
        if e.button() == Qt.LeftButton and not moved and self.mode == self.MODE_PREVIEW:
            uid = self.pick(e.position())
            if uid is not None:
                self.previewObjectClicked.emit(uid)
            return
        if self._drag is not None:
            if moved:
                self.objectDragFinished.emit(self._drag[0])
            elif click_select is not None:
                self.objectClicked.emit(click_select, 0)
            self._drag = None
            return
        if e.button() == Qt.LeftButton and not moved and self.mode == self.MODE_PREPARE and not self.pick_mode:
            self.objectClicked.emit(None, 0)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        self.camera.distance = float(np.clip(self.camera.distance * (0.88 ** steps), 5.0, 5000.0))
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.fit_objects()
