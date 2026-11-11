"""The program's icon: the Prodentix fox standing on UniSlicer's three layers, in the Prodentix colours
(magenta #eb1a91, black) on a white tile. Drawn from the brand mark in unislicer/gui/brand, then written
everywhere it is used:

    packaging/unislicer.ico        the exe, the installer and its shortcuts (16 - 256 px)
    packaging/unislicer.png
    unislicer/gui/brand/app_icon.png   the window and the taskbar while the program runs
    remote_web/icon-*.png          the phone control page (and its home-screen icon)

    python tools/make_icon.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024
PINK = (235, 26, 145)
PINK_SOFT = (245, 150, 200)
BLACK = (11, 11, 12)
WHITE = (255, 255, 255)


def _mark(width: int) -> Image.Image:
    mark = Image.open(os.path.join(HERE, "unislicer", "gui", "brand", "prodentix_mark.png")).convert("RGBA")
    mark = mark.crop(mark.getbbox())
    alpha = mark.split()[3].filter(ImageFilter.MaxFilter(11))      # a bolder line, to read at small sizes
    k = width / mark.width
    alpha = alpha.resize((int(mark.width * k), int(mark.height * k)), Image.LANCZOS)
    out = Image.new("RGBA", alpha.size, PINK + (0,))
    out.putalpha(alpha)
    return out


def _art(scale: float = 1.0) -> Image.Image:
    """The fox and the layers on a transparent square (``scale`` < 1 leaves a margin round them)."""
    art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(art)
    cx, lw = 512, 46
    r = lw / 2
    w, h, gap, top = 300, 145, 112, 590
    for k, col in reversed(list(enumerate((BLACK, PINK, PINK_SOFT)))):
        y = top + k * gap
        if k == 0:                                   # the top layer, filled
            d.polygon([(cx - w - r, y), (cx, y - h - r * 0.9), (cx + w + r, y), (cx, y + h + r * 0.9)], fill=col)
        else:                                        # the layers below it
            d.line([(cx - w, y), (cx, y + h), (cx + w, y)], fill=col, width=lw, joint="curve")
            for px in (cx - w, cx + w):
                d.ellipse((px - r, y - r, px + r, y + r), fill=col)
    fox = _mark(440)
    art.alpha_composite(fox, (cx - fox.width // 2, 260 - fox.height // 2))
    if scale != 1.0:
        n = int(S * scale)
        small = art.resize((n, n), Image.LANCZOS)
        art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        art.alpha_composite(small, ((S - n) // 2, (S - n) // 2))
    return art


def icon() -> Image.Image:
    """The icon on its rounded white tile, 1024 px."""
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), 220, fill=255)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), mask)
    tile.alpha_composite(_art())
    return tile


def maskable() -> Image.Image:
    """For phones that cut their own shape out of the icon: white to the edges, the art inside the safe circle."""
    out = Image.new("RGBA", (S, S), WHITE + (255,))
    out.alpha_composite(_art(0.72))
    return out


def main() -> None:
    big = icon()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [big.resize((n, n), Image.LANCZOS) for n in sizes]
    frames[-1].save(os.path.join(HERE, "packaging", "unislicer.ico"), sizes=[(n, n) for n in sizes],
                    append_images=frames[:-1])
    frames[-1].save(os.path.join(HERE, "packaging", "unislicer.png"))
    frames[-1].save(os.path.join(HERE, "unislicer", "gui", "brand", "app_icon.png"))
    web = os.path.join(HERE, "remote_web")
    big.resize((192, 192), Image.LANCZOS).save(os.path.join(web, "icon-192.png"))
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(web, "icon-512.png"))
    maskable().resize((512, 512), Image.LANCZOS).save(os.path.join(web, "icon-maskable-512.png"))
    print("icons written")


if __name__ == "__main__":
    main()
