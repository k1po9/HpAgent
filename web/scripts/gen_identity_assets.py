#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Subset the Identity Terminal fonts to Basic Latin WOFF2.

CJK text uses system fonts, so these stay small. Requires fonttools and brotli.
Usage: gen_identity_assets.py <rhine-lock checkout>
"""
import pathlib
import sys

from fontTools import subset

PUBLIC = pathlib.Path(__file__).resolve().parents[1] / "public" / "identity"
FONTS = (
    "Montserrat-Light",
    "Montserrat-Medium",
    "Montserrat-SemiBold",
    "Montserrat-Bold",
    "JetBrainsMono-Regular",
)


def subset_font(source: pathlib.Path, target: pathlib.Path) -> None:
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["kern", "liga"]
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=range(0x20, 0x7F))
    subsetter.subset(font)
    subset.save_font(font, str(target), options)


def main() -> None:
    upstream = pathlib.Path(sys.argv[1]) / "frontend" / "public"
    for name in FONTS:
        subset_font(upstream / "fonts" / f"{name}.ttf", PUBLIC / "fonts" / f"{name}.woff2")


if __name__ == "__main__":
    main()
