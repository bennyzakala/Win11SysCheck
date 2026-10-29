"""Elegoo .goo (V3.0) writer and reader.

Layout from Elegoo's published "Goo Format Spec V1.2", cross-checked with the open-source UVtools
implementation (GooFile.cs): big-endian, a fixed-size header with two RGB565 previews, then per layer a
definition block, the RLE image (0x55 ... checksum) and a 0x0D 0x0A delimiter, then a footer.
"""
from __future__ import annotations

import struct
import time
from typing import Callable, Iterable

import numpy as np
from PIL import Image

from . import __version__
from .settings import Config
from .sla_timing import layer_light_off, layer_pwm, layer_rests

VERSION = b"V3.0"
MAGIC = bytes([0x07, 0x00, 0x00, 0x00, 0x44, 0x4C, 0x50, 0x00])
DELIMITER = b"\r\n"
LAYER_MAGIC = 0x55
SMALL_PREVIEW = (116, 116)
BIG_PREVIEW = (290, 290)

HEADER_TEXT = struct.Struct(">4s8s32s24s24s32s32s32sHHH")
HEADER_PRINT = struct.Struct(">IHH??fffff?ffffffffIffffffffffffffffHH?Ifff8sI?H")
LAYER_DEF = struct.Struct(">Hfffffffffffffff H2sI")   # 70 bytes, followed by the RLE data and a delimiter
FOOTER = struct.Struct(">3x8s")
HEADER_SIZE = HEADER_TEXT.size + 2 * 116 * 116 + 2 + 2 * 290 * 290 + 2 + HEADER_PRINT.size

assert HEADER_SIZE == 195477 and LAYER_DEF.size == 70 and FOOTER.size == 11


def _text(value: str, size: int) -> bytes:
    return value.encode("utf-8")[:size - 1].ljust(size, b"\0")


# ------------------------------------------------------------------ RLE
def _chunk(out: bytearray, value: int, length: int) -> None:
    while length > 0:
        n = min(length, 0xFFFFFFF)
        length -= n
        kind = 0 if value == 0 else 3 if value == 255 else 1
        extra = 0 if n <= 0xF else 1 if n <= 0xFFF else 2 if n <= 0xFFFFF else 3
        out.append((kind << 6) | (extra << 4) | (n & 0xF))
        if kind == 1:
            out.append(value)
        if extra == 3:
            out += bytes(((n >> 20) & 0xFF, (n >> 12) & 0xFF, (n >> 4) & 0xFF))
        elif extra == 2:
            out += bytes(((n >> 12) & 0xFF, (n >> 4) & 0xFF))
        elif extra == 1:
            out.append((n >> 4) & 0xFF)


def encode_layer(pixels: np.ndarray) -> bytes:
    """GOO layer image: 0x55, runs of black / grey / white pixels, then the 8-bit checksum."""
    flat = np.asarray(pixels, dtype=np.uint8).ravel()
    out = bytearray([LAYER_MAGIC])
    if flat.size:
        change = np.flatnonzero(np.diff(flat)) + 1
        starts = np.concatenate([[0], change])
        lengths = np.diff(np.concatenate([starts, [flat.size]]))
        for s, n in zip(starts.tolist(), lengths.tolist()):
            _chunk(out, int(flat[s]), n)
    out.append(~sum(out[1:]) & 0xFF)
    return bytes(out)


def decode_layer(data: bytes, size: int) -> np.ndarray:
    if not data or data[0] != LAYER_MAGIC:
        raise ValueError("missing GOO layer magic")
    if (~sum(data[1:-1]) & 0xFF) != data[-1]:
        raise ValueError("GOO layer checksum mismatch")
    pixels = np.zeros(size, dtype=np.uint8)
    pos, i, prev = 0, 1, 0
    end = len(data) - 1
    while i < end:
        b0 = data[i]
        kind, extra, nib = b0 >> 6, (b0 >> 4) & 3, b0 & 0xF
        i += 1
        if kind == 2:  # difference from the previous pixel (we never write these, but can read them)
            n = 1
            if extra & 1:
                n = data[i]
                i += 1
            value = prev - nib if extra & 2 else prev + nib
        else:
            value = 0 if kind == 0 else 255 if kind == 3 else data[i]
            if kind == 1:
                i += 1
            n = nib
            if extra == 1:
                n |= data[i] << 4
            elif extra == 2:
                n |= (data[i] << 12) | (data[i + 1] << 4)
            elif extra == 3:
                n |= (data[i] << 20) | (data[i + 1] << 12) | (data[i + 2] << 4)
            i += extra
        pixels[pos:pos + n] = value
        pos += n
        prev = value
    return pixels


# --------------------------------------------------------------- previews
def _rgb565(img: Image.Image, size: tuple[int, int]) -> bytes:
    rgb = np.asarray(img.convert("RGB").resize(size), dtype=np.uint16)
    v = ((rgb[..., 0] >> 3) << 11) | ((rgb[..., 1] >> 2) << 5) | (rgb[..., 2] >> 3)
    return v.astype(">u2").tobytes()


# ------------------------------------------------------------------ write
def write(path: str, layer_rle: Iterable[tuple[int, bytes]], layer_count: int, cfg: Config, *,
          layer_height: float, exposures: list[float], volume_ml: float, weight_g: float, cost: float,
          print_time_s: float, preview: Image.Image, machine_name: str = "UniSlicer",
          progress: Callable[[float], None] | None = None) -> None:
    """Write a GOO file. ``layer_rle`` yields (index, bytes from :func:`encode_layer`) in order."""
    bottom = min(int(cfg["bottom_layers"]), layer_count)
    tsmc = bool(cfg["tsmc_enable"])

    def motion(is_bottom: bool):
        lift = cfg["bottom_lift_height"] if is_bottom else cfg["lift_height"]
        lift_speed = cfg["bottom_lift_speed"] if is_bottom else cfg["lift_speed"]
        retract_speed = cfg["bottom_retract_speed"] if is_bottom else cfg["retract_speed"]
        if tsmc:
            lift2, speed2 = cfg["lift_height_2"], cfg["lift_speed_2"]
            slow = min(cfg["retract_height_2"], lift + lift2)
            return (lift, lift_speed, lift2, speed2, lift + lift2 - slow, retract_speed, slow, cfg["retract_speed_2"])
        return (lift, lift_speed, 0.0, lift_speed, lift, retract_speed, 0.0, retract_speed)

    rest = (cfg["rest_before_lift"], cfg["rest_after_lift"], cfg["rest_after_retract"])
    bottom_rest = (cfg["bottom_rest_before_lift"], cfg["bottom_rest_after_lift"], cfg["bottom_rest_after_retract"])
    bm, nm = motion(True), motion(False)
    pwm = int(cfg["light_pwm"])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with open(path, "wb") as fh:
        fh.write(HEADER_TEXT.pack(
            VERSION, MAGIC, _text("UniSlicer", 32), _text(__version__, 24), _text(stamp, 24),
            _text(machine_name, 32), _text("DLP", 32), _text(cfg["material_name"], 32),
            int(cfg["antialiasing"]), 1, int(cfg["image_blur"])))
        fh.write(_rgb565(preview, SMALL_PREVIEW) + DELIMITER)
        fh.write(_rgb565(preview, BIG_PREVIEW) + DELIMITER)
        fh.write(HEADER_PRINT.pack(
            layer_count, int(cfg["resolution_x"]), int(cfg["resolution_y"]), bool(cfg["mirror_x"]), bool(cfg["mirror_y"]),
            cfg["bed_x"], cfg["bed_y"], cfg["max_z"], layer_height, cfg["exposure_time"],
            cfg["wait_mode"] != "light_off", cfg["light_off_delay"], *bottom_rest, *rest, cfg["bottom_exposure_time"], bottom,
            bm[0], bm[1], nm[0], nm[1], bm[4], bm[5], nm[4], nm[5],
            bm[2], bm[3], nm[2], nm[3], bm[6], bm[7], nm[6], nm[7],
            int(cfg["light_pwm_bottom"]), pwm, True, int(print_time_s), volume_ml * 1000.0, weight_g, cost, _text("$", 8),
            HEADER_SIZE, True, int(cfg["transition_layers"])))
        assert fh.tell() == HEADER_SIZE
        count = 0
        for i, rle in layer_rle:
            m = bm if i < bottom else nm
            fh.write(LAYER_DEF.pack(0, cfg["max_z"], round((i + 1) * layer_height, 5), exposures[i],
                                    layer_light_off(i, cfg), *layer_rests(i, cfg), *m, layer_pwm(i, cfg),
                                    DELIMITER, len(rle)))
            fh.write(rle)
            fh.write(DELIMITER)
            count += 1
            if progress and (i % 10 == 0 or i == layer_count - 1):
                progress((i + 1) / layer_count)
        if count != layer_count:
            raise ValueError("layer count mismatch")
        fh.write(FOOTER.pack(MAGIC))


# ------------------------------------------------------------------- read
def read(path: str) -> dict:
    """Parse a GOO file (used by the tests and for diagnostics)."""
    with open(path, "rb") as fh:
        data = fh.read()
    text = HEADER_TEXT.unpack_from(data, 0)
    off = HEADER_TEXT.size + 2 * 116 * 116 + 2 + 2 * 290 * 290 + 2
    p = HEADER_PRINT.unpack_from(data, off)
    header = {"version": text[0], "magic": text[1], "machine": text[5].rstrip(b"\0").decode(),
              "layer_count": p[0], "res_x": p[1], "res_y": p[2], "layer_height": p[8],
              "bottom_layers": p[19], "layer_offset": p[-3], "transition_layers": p[-1]}
    layers = []
    pos = header["layer_offset"]
    for _ in range(header["layer_count"]):
        d = LAYER_DEF.unpack_from(data, pos)
        pos += LAYER_DEF.size
        size = d[-1]
        layers.append({"z": d[2], "exposure": d[3], "lift": d[8], "rle": data[pos:pos + size]})
        pos += size
        if data[pos:pos + 2] != DELIMITER:
            raise ValueError("missing layer delimiter")
        pos += 2
    footer_ok = data[pos:] == FOOTER.pack(MAGIC)
    return {"header": header, "layers": layers, "footer_ok": footer_ok}
