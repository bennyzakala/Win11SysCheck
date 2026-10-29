"""Chitubox .ctb (version 4) writer and reader.

Layout follows the open-source UVtools implementation (UVtools.Core/FileFormats/ChituboxFile.cs,
CtbRleCodec.cs, FileFormat.cs) and the catibo format notes. All numbers are little-endian except the
run lengths inside layer RLE data, which are big-endian.
"""
from __future__ import annotations

import random
import struct
import time
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageDraw

from .settings import Config

MAGIC_CTB_V4 = 0x12FD0106
VERSION = 4
SOFTWARE_VERSION = 0x1090000          # "1.9.0", what CTB v4 readers expect
PER_LAYER_SETTINGS = VERSION * 0x10    # 0x40: use the per-layer lift/exposure table
DISCLAIMER = ("Layout and record format for the ctb and cbddlp file types are the copyrighted programs or codes "
              "of CBD Technology (China) Inc..The Customer or User shall not in any manner reproduce, distribute, "
              "modify, decompile, disassemble, decrypt, extract, reverse engineer, lease, assign, or sublicense the "
              "said programs or codes.").encode("utf-8")
RESERVED_V4 = 380

HEADER = struct.Struct("<IIfffIIfffffIIIIIIIIIIIIHHIII")         # 112 bytes
PRINT_PARAMS = struct.Struct("<10f5I")                           # 60 bytes
SLICER_INFO = struct.Struct("<7fIIBHBIIIffIIII")                 # 76 bytes + machine name
PRINT_PARAMS_V4 = struct.Struct("<ffIfIffffffIIIIIIIIII")        # 84 bytes + 380 reserved
PREVIEW = struct.Struct("<8I")                                   # 32 bytes + RGB15 RLE data
LAYER_DEF = struct.Struct("<fffIIIIII")                          # 36 bytes
LAYER_DEF_EX = struct.Struct("<I11f")                            # 48 bytes (after a LayerDef copy)
LAYER_TABLE_SIZE = LAYER_DEF.size + LAYER_DEF_EX.size            # 84
MAX_RUN = 0xFFFFFFF

assert HEADER.size == 112 and PRINT_PARAMS.size == 60 and SLICER_INFO.size == 76
assert PRINT_PARAMS_V4.size == 84 and LAYER_DEF.size == 36 and LAYER_DEF_EX.size == 48 and len(DISCLAIMER) == 320


# ------------------------------------------------------------------ RLE
def _add_run(out: bytearray, stride: int, grey7: int) -> None:
    while stride > MAX_RUN:
        _add_run(out, MAX_RUN, grey7)
        stride -= MAX_RUN
    if stride <= 0:
        return
    if stride == 1:
        out.append(grey7)
        return
    out.append(grey7 | 0x80)
    if stride <= 0x7F:
        out.append(stride)
    elif stride <= 0x3FFF:
        out += bytes(((stride >> 8) | 0x80, stride & 0xFF))
    elif stride <= 0x1FFFFF:
        out += bytes(((stride >> 16) | 0xC0, (stride >> 8) & 0xFF, stride & 0xFF))
    else:
        out += bytes(((stride >> 24) | 0xE0, (stride >> 16) & 0xFF, (stride >> 8) & 0xFF, stride & 0xFF))


def encode_layer(pixels: np.ndarray) -> bytes:
    """7-bit grey run-length encoding of an 8-bit image (row-major)."""
    grey = (np.asarray(pixels, dtype=np.uint8).ravel() >> 1).astype(np.int16)
    if grey.size == 0:
        return b""
    change = np.flatnonzero(np.diff(grey)) + 1
    starts = np.concatenate([[0], change])
    lengths = np.diff(np.concatenate([starts, [grey.size]]))
    out = bytearray()
    for s, n in zip(starts.tolist(), lengths.tolist()):
        _add_run(out, n, int(grey[s]))
    return bytes(out)


def decode_layer(rle: bytes, size: int) -> np.ndarray:
    pixels = np.zeros(size, dtype=np.uint8)
    pos, n = 0, 0
    while n < len(rle):
        code = rle[n]
        stride = 1
        if code & 0x80:
            code &= 0x7F
            n += 1
            b = rle[n]
            if b & 0x80 == 0:
                stride = b
            elif b & 0xC0 == 0x80:
                stride = ((b & 0x3F) << 8) | rle[n + 1]
                n += 1
            elif b & 0xE0 == 0xC0:
                stride = ((b & 0x1F) << 16) | (rle[n + 1] << 8) | rle[n + 2]
                n += 2
            elif b & 0xF0 == 0xE0:
                stride = ((b & 0x0F) << 24) | (rle[n + 1] << 16) | (rle[n + 2] << 8) | rle[n + 3]
                n += 3
            else:
                raise ValueError("corrupted RLE data")
        if code:
            pixels[pos:pos + stride] = (code << 1) | 1
        pos += stride
        n += 1
    return pixels


def crypt_layer(seed: int, layer_index: int, data: bytes) -> bytes:
    """XOR keystream used by encrypted CTB layers (same function encrypts and decrypts)."""
    if seed == 0:
        return data
    m = 0xFFFFFFFF
    init = (seed * 0x2D83CDAC + 0xD8A83423) & m
    key = (((layer_index * 0x1E1530CD + 0xEC3D47CD) & m) * init) & m
    buf = bytearray(data)
    words = len(buf) // 4
    if words:
        keys = (key + init * np.arange(words, dtype=np.uint64)) & m
        arr = np.frombuffer(bytes(buf[:words * 4]), dtype="<u4") ^ keys.astype("<u4")
        buf[:words * 4] = arr.astype("<u4").tobytes()
        key = (key + init * words) & m
    for i, shift in zip(range(words * 4, len(buf)), range(0, 32, 8)):
        buf[i] ^= (key >> shift) & 0xFF
    return bytes(buf)


# --------------------------------------------------------------- previews
def encode_preview(img: Image.Image) -> bytes:
    """RGB15 run-length encoding used for the printer's thumbnail."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint16).reshape(-1, 3)
    c15 = (rgb[:, 2] >> 3) | ((rgb[:, 1] >> 2) << 5) | ((rgb[:, 0] >> 3) << 11)
    out = bytearray()

    def emit(rep: int, color: int):
        if rep == 0:
            return
        if rep <= 2:
            out.extend(struct.pack("<H", color & ~0x20) * rep)
        else:
            out.extend(struct.pack("<HH", color | 0x20, (rep - 1) | 0x3000))

    change = np.flatnonzero(np.diff(c15)) + 1
    starts = np.concatenate([[0], change])
    lengths = np.diff(np.concatenate([starts, [c15.size]]))
    for s, n in zip(starts.tolist(), lengths.tolist()):
        color = int(c15[s])
        while n > 0:
            rep = min(n, 0xFFE)
            emit(rep, color)
            n -= rep
    return bytes(out)


def make_preview(layers, cfg: Config, size: tuple[int, int]) -> Image.Image:
    """Top view of the print (union of all layers) for the printer's screen."""
    from .slicing import polygons_of
    w, h = size
    img = Image.new("RGB", size, (24, 26, 32))
    draw = ImageDraw.Draw(img)
    sx, sy = w / cfg["bed_x"], h / cfg["bed_y"]
    step = max(1, len(layers) // 60)
    shade = len(range(0, len(layers), step)) or 1
    for k, i in enumerate(range(0, len(layers), step)):
        t = k / shade
        color = (int(60 + 70 * t), int(110 + 80 * t), int(200 + 55 * t))
        for p in polygons_of(layers[i]):
            pts = [(x * sx, (cfg["bed_y"] - y) * sy) for x, y in np.asarray(p.exterior.coords)[:, :2]]
            if len(pts) >= 3:
                draw.polygon(pts, fill=color)
    return img


# ------------------------------------------------------------------ write
def write(path: str, layer_rle: Iterable[tuple[int, bytes]], layer_count: int, cfg: Config, *,
          layer_height: float, exposures: list[float], volume_ml: float, weight_g: float, cost: float,
          print_time_s: float, preview_layers, machine_name: str = "UniSlicer", encrypt: bool = True,
          progress: Callable[[float], None] | None = None) -> None:
    """Write a CTB v4 file. ``layer_rle`` yields (index, RLE bytes from :func:`encode_layer`) in order."""
    bottom = min(int(cfg["bottom_layers"]), layer_count)
    seed = random.randint(0xFF, 0x7FFFFFFF) if encrypt else 0
    name = machine_name.encode("utf-8")
    tsmc = bool(cfg["tsmc_enable"])
    lift2 = cfg["lift_height_2"] if tsmc else 0.0
    speed2 = cfg["lift_speed_2"] if tsmc else cfg["lift_speed"]
    ret2 = cfg["retract_height_2"] if tsmc else 0.0
    rspeed2 = cfg["retract_speed_2"] if tsmc else cfg["retract_speed"]
    aa = int(cfg["antialiasing"])

    with open(path, "wb") as fh:
        fh.write(b"\0" * HEADER.size)  # header is written last, once all offsets are known

        offsets = {}
        for key, size in (("large", (400, 300)), ("small", (200, 125))):
            offsets[key] = fh.tell()
            data = encode_preview(make_preview(preview_layers, cfg, size))
            fh.write(PREVIEW.pack(size[0], size[1], fh.tell() + PREVIEW.size, len(data), 0, 0, 0, 0))
            fh.write(data)

        print_params_offset = fh.tell()
        fh.write(PRINT_PARAMS.pack(
            cfg["bottom_lift_height"], cfg["bottom_lift_speed"], cfg["lift_height"], cfg["lift_speed"],
            cfg["retract_speed"], volume_ml, weight_g, cost, cfg["light_off_delay"], cfg["light_off_delay"],
            bottom, 0, 0, 0, 0))

        slicer_offset = fh.tell()
        v4_address = slicer_offset + SLICER_INFO.size + len(name) + len(DISCLAIMER)
        fh.write(SLICER_INFO.pack(
            lift2, speed2, lift2, speed2, ret2, rspeed2, cfg["rest_after_lift"],
            slicer_offset + SLICER_INFO.size, len(name), 0x0F, 0, PER_LAYER_SETTINGS,
            int(time.time() // 60) & 0xFFFFFFFF, aa, SOFTWARE_VERSION, cfg["light_off_delay"], 0.0,
            int(cfg["transition_layers"]), v4_address, 0, 0))
        fh.write(name)
        disclaimer_address = fh.tell()
        fh.write(DISCLAIMER)
        assert fh.tell() == v4_address
        fh.write(PRINT_PARAMS_V4.pack(
            cfg["bottom_retract_speed"], rspeed2, 0, 4.0, 0, 4.0, cfg["light_off_delay"], cfg["rest_after_lift"],
            cfg["rest_before_lift"], ret2, 0.0, 0, 5, max(0, layer_count - 1), 0, 0, 0, 0,
            disclaimer_address, len(DISCLAIMER), 0))
        fh.write(b"\0" * RESERVED_V4)

        table_offset = fh.tell()
        fh.write(b"\0" * (LAYER_DEF.size * layer_count))
        table = []
        for i, rle in layer_rle:
            rle = crypt_layer(seed, i, rle)
            is_bottom = i < bottom
            z = round((i + 1) * layer_height, 5)
            ex_offset = fh.tell()
            data_address = ex_offset + LAYER_TABLE_SIZE
            layer_def = LAYER_DEF.pack(z, exposures[i], cfg["light_off_delay"], data_address, len(rle), 0,
                                       LAYER_TABLE_SIZE, 0, 0)
            fh.write(layer_def)
            fh.write(LAYER_DEF_EX.pack(
                LAYER_TABLE_SIZE + len(rle),
                cfg["bottom_lift_height"] if is_bottom else cfg["lift_height"],
                cfg["bottom_lift_speed"] if is_bottom else cfg["lift_speed"],
                lift2, speed2,
                cfg["bottom_retract_speed"] if is_bottom else cfg["retract_speed"],
                ret2, rspeed2, cfg["rest_before_lift"], cfg["rest_after_lift"], cfg["light_off_delay"],
                float(cfg["light_pwm"])))
            fh.write(rle)
            table.append(layer_def)
            if progress and (i % 10 == 0 or i == layer_count - 1):
                progress((i + 1) / layer_count)
        if len(table) != layer_count:
            raise ValueError("layer count mismatch")
        end = fh.tell()
        fh.seek(table_offset)
        fh.write(b"".join(table))

        fh.seek(0)
        fh.write(HEADER.pack(
            MAGIC_CTB_V4, VERSION, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], 0, 0,
            round(layer_count * layer_height, 5), layer_height, cfg["exposure_time"], cfg["bottom_exposure_time"],
            cfg["light_off_delay"], bottom, int(cfg["resolution_x"]), int(cfg["resolution_y"]),
            offsets["large"], table_offset, layer_count, offsets["small"], int(print_time_s), 0,
            print_params_offset, PRINT_PARAMS.size, 1, int(cfg["light_pwm"]), int(cfg["light_pwm"]), seed,
            slicer_offset, SLICER_INFO.size))
        fh.seek(end)


# ------------------------------------------------------------------- read
def read(path: str) -> dict:
    """Parse a CTB file written by :func:`write` (used by the tests and for diagnostics)."""
    with open(path, "rb") as fh:
        data = fh.read()
    h = HEADER.unpack_from(data, 0)
    keys = ("magic version bed_x bed_y bed_z u1 u2 height layer_height exposure bottom_exposure light_off "
            "bottom_layers res_x res_y preview_large table layer_count preview_small print_time projector "
            "print_params print_params_size aa pwm bottom_pwm seed slicer_offset slicer_size").split()
    header = dict(zip(keys, h))
    slicer = SLICER_INFO.unpack_from(data, header["slicer_offset"])
    name_addr, name_len = slicer[7], slicer[8]
    v4 = PRINT_PARAMS_V4.unpack_from(data, slicer[18])
    layers = []
    for i in range(header["layer_count"]):
        z, exposure, off, addr, size, page, tsize, _, _ = LAYER_DEF.unpack_from(data, header["table"] + i * LAYER_DEF.size)
        rle = crypt_layer(header["seed"], i, data[addr:addr + size])
        layers.append({"z": z, "exposure": exposure, "rle": rle, "table_size": tsize,
                       "ex": LAYER_DEF_EX.unpack_from(data, addr - LAYER_DEF_EX.size)})
    return {"header": header, "machine": data[name_addr:name_addr + name_len].decode(),
            "transition_layers": slicer[17], "disclaimer": data[v4[18]:v4[18] + v4[19]], "layers": layers}
