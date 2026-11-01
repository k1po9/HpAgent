#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Adapted from rhine-lock tools/assets/gen_textures.py (acrylic_card) at a4df68c.
# Modified 2026-11 for HpAgent: the upstream ∞ mark is replaced by the HpAgent wordmark.
"""Generate the card-stream face. Usage: gen_identity_card.py <rhine-lock checkout>"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

PUBLIC = pathlib.Path(__file__).resolve().parents[1] / "public" / "identity"
SS = 4
BARS = (3, 1, 2, 1, 3, 2, 1, 1, 3, 1, 2)


def main() -> None:
    W, H, R = 512, 368, 26
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W * SS - 1, H * SS - 1), R * SS, fill=(244, 243, 239, 255))
    d.rounded_rectangle(
        (6 * SS, 6 * SS, W * SS - 6 * SS, H * SS - 6 * SS),
        (R - 6) * SS,
        outline=(214, 211, 204, 255),
        width=2 * SS,
    )
    img = img.resize((W, H), Image.LANCZOS)
    # milky vertical falloff
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: 255 - v // 10)
    px, sp = img.load(), shade.load()
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            k = sp[x, y] / 255
            px[x, y] = (int(r * k), int(g * k), int(b * k), a)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(
        str(pathlib.Path(sys.argv[1]) / "frontend/public/fonts/Montserrat-SemiBold.ttf"), 64
    )
    d.text((58, H // 2 - 48), "HpAgent", font=font, fill=(176, 172, 164, 255))
    for i, w in enumerate(BARS):
        x0 = W - 128 + sum(BARS[:i]) * 3 + i * 2
        d.rectangle((x0, 40, x0 + w * 3 - 1, 74), fill=(150, 148, 142, 255))
    d.rectangle((W - 128, 84, W - 52, 87), fill=(170, 168, 162, 255))
    d.rectangle((60, H - 70, 230, H - 66), fill=(186, 183, 176, 255))
    d.rectangle((60, H - 58, 170, H - 55), fill=(206, 203, 197, 255))
    d.ellipse((W - 70, H - 72, W - 46, H - 48), outline=(186, 183, 176, 255), width=3)
    img.save(PUBLIC / "card.png")


if __name__ == "__main__":
    main()
