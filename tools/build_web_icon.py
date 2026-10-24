"""Значок веб-клиента (вкладка браузера и ярлык на рабочем столе телефона): лицо орка с обложки правил.

Запускается один раз, вручную; нужны Pillow и pdftoppm (poppler-utils):
    pip install pillow
    python tools/build_web_icon.py

Источник: docs/rules/heroes1-rules.pdf, с. 1 (обложка, 400 dpi) — герои фракций по правому краю,
орк — третий сверху. Результат — heroes/web/static/icon-192.png и icon-512.png (их называет manifest.webmanifest).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "docs" / "rules" / "heroes1-rules.pdf"
OUT = ROOT / "heroes" / "web" / "static"
DPI = 400
BOX = (950, 730, 1290, 1070)   # квадрат с лицом орка в координатах страницы при 200 dpi
SIZES = (192, 512)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", str(DPI), "-png", str(PDF), f"{tmp}/cover"], check=True)
        page = Image.open(next(Path(tmp).glob("cover*.png"))).convert("RGB")
    k = DPI / 200
    face = page.crop(tuple(round(v * k) for v in BOX))
    for size in SIZES:
        face.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png", optimize=True)
        print(f"icon-{size}.png")


if __name__ == "__main__":
    main()
