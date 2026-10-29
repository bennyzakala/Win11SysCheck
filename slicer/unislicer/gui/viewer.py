"""OpenGL 3.3 viewport: build plate, models, SLA supports and shaded toolpath preview."""
from __future__ import annotations

import ctypes
import math
import sys

import numpy as np
from OpenGL import GL
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QSurfaceFormat
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
uniform mat4 uMVP; out vec3 vNormal; out vec3 vPos;
void main(){ vNormal = aNormal; vPos = aPos; gl_Position = uMVP * vec4(aPos, 1.0); }"""
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
in vec2 vXY; out vec4 frag; uniform vec2 uBed; uniform vec3 uBase;
float grid(vec2 p, float s, float w){ vec2 c = abs(fract(p / s - 0.5) - 0.5) * s / max(fwidth(p), vec2(1e-4)); return 1.0 - clamp(min(c.x, c.y) - w, 0.0, 1.0); }
void main(){
  vec3 c = uBase;
  c = mix(c, vec3(0.30, 0.34, 0.42), grid(vXY, 10.0, 0.0) * 0.35);
  c = mix(c, vec3(0.38, 0.44, 0.56), grid(vXY, 50.0, 0.4) * 0.55);
  vec2 e = min(vXY, uBed - vXY); float edge = min(e.x, e.y);
  c = mix(c, vec3(0.31, 0.55, 1.0), 1.0 - smoothstep(0.0, max(fwidth(edge) * 2.0, 0.4), edge));
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
    objectClicked = Signal(object)            # uid or None
    objectDragged = Signal(object, float, float)
    objectDragFinished = Signal(object)
    glFailed = Signal(str)

    MODE_PREPARE, MODE_PREVIEW = "prepare", "preview"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.StrongFocus)
        self.camera = Camera()
        self.bed = (220.0, 220.0, 250.0)
        self.technology = "fdm"
        self.mode = self.MODE_PREPARE
        self.objects: dict = {}          # uid -> dict(mesh, buffer, color, dirty)
        self.selected = None
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
        self._press_pos = QPointF()

    @staticmethod
    def configure_default_format():
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

    # ---------------------------------------------------------- public API
    def set_bed(self, x: float, y: float, z: float, technology: str):
        self.bed = (float(x), float(y), float(z))
        self.technology = technology
        self._bed_dirty = True
        self.update()

    def set_object(self, uid, mesh: Mesh, state: str = "normal"):
        old = self.objects.get(uid)
        if old and old.get("buffer"):
            self._trash(old["buffer"])
        self.objects[uid] = {"mesh": mesh, "buffer": None, "state": state}
        self.update()

    def set_object_state(self, uid, state: str):
        if uid in self.objects:
            self.objects[uid]["state"] = state
            self.update()

    def remove_object(self, uid):
        obj = self.objects.pop(uid, None)
        if obj and obj.get("buffer"):
            self._trash(obj["buffer"])
        self.update()

    def set_selected(self, uid):
        self.selected = uid
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
        bx, by, _ = self.bed
        return np.array([bx / 2, by / 2, 0.0])

    def view_preset(self, name: str):
        c = self.camera
        bx, by, bz = self.bed
        c.target = self._scene_center() + np.array([0, 0, min(bz, 60) * 0.3])
        c.distance = max(bx, by, bz * 0.8) * 2.05 + 30
        c.yaw, c.pitch = {"iso": (-62.0, 30.0), "top": (-90.0, 89.9), "front": (-90.0, 6.0), "side": (0.0, 6.0)}[name]
        self.update()

    def fit_objects(self):
        meshes = [o["mesh"] for o in self.objects.values()]
        if not meshes:
            self.view_preset("iso")
            return
        lo = np.min([m.bounds[0] for m in meshes], axis=0)
        hi = np.max([m.bounds[1] for m in meshes], axis=0)
        self.camera.target = (lo + hi) / 2
        radius = np.linalg.norm(hi - lo) / 2
        self.camera.distance = max(30.0, radius / math.sin(math.radians(self.camera.fov / 2)) * 1.15)
        self.update()

    # ---------------------------------------------------------- GL lifecycle
    def initializeGL(self):
        try:
            fmt = self.context().format()
            if (fmt.majorVersion(), fmt.minorVersion()) < (3, 3):
                raise RuntimeError(f"OpenGL {fmt.majorVersion()}.{fmt.minorVersion()} context; 3.3 is required")
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
        base = (0.105, 0.118, 0.145) if self.technology == "fdm" else (0.11, 0.105, 0.14)
        GL.glUniform3f(self._u(self._bed_prog, "uBase"), *base)
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
                color = theme.MODEL_OUTSIDE if state == "outside" else (
                    theme.MODEL_SELECTED if uid == self.selected else theme.MODEL_COLOR)
                GL.glUniform3f(self._u(p, "uColor"), *color)
                obj["buffer"].draw(GL.GL_TRIANGLES)
        GL.glUniform1i(self._u(p, "uShowOverhang"), 0)
        if preview or self.technology == "sla":
            for buf, color in self._extra_buffers:
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
        return float(dist[hit].min()) if hit.any() else None

    def pick(self, pos: QPointF):
        origin, direction = self._ray(pos)
        best, best_d = None, np.inf
        for uid, obj in self.objects.items():
            d = self._ray_mesh(origin, direction, obj["mesh"])
            if d is not None and d < best_d:
                best, best_d = uid, d
        return best

    def _bed_point(self, pos: QPointF, z: float = 0.0):
        origin, direction = self._ray(pos)
        if abs(direction[2]) < 1e-6:
            return None
        t = (z - origin[2]) / direction[2]
        return None if t < 0 else origin + direction * t

    # ---------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        self._last = e.position()
        self._press_pos = e.position()
        self._drag = None
        if e.button() == Qt.LeftButton and self.mode == self.MODE_PREPARE:
            uid = self.pick(e.position())
            if uid is not None:
                self.objectClicked.emit(uid)
                start = self._bed_point(e.position())
                if start is not None:
                    self._drag = (uid, start)

    def mouseMoveEvent(self, e):
        pos = e.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
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

    def mouseReleaseEvent(self, e):
        moved = (e.position() - self._press_pos).manhattanLength() > 3
        if self._drag is not None:
            if moved:
                self.objectDragFinished.emit(self._drag[0])
            self._drag = None
            return
        if e.button() == Qt.LeftButton and not moved and self.mode == self.MODE_PREPARE:
            self.objectClicked.emit(None)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        self.camera.distance = float(np.clip(self.camera.distance * (0.88 ** steps), 5.0, 5000.0))
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.fit_objects()
