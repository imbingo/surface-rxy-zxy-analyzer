from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ICON_PATH = ASSET_DIR / "SurfaceRxyZxyAnalyzer.ico"
PNG_PATH = ASSET_DIR / "SurfaceRxyZxyAnalyzer.png"


def main() -> None:
    size = 512
    image = Image.new("RGBA", (size, size), "#18364a")
    draw = ImageDraw.Draw(image)

    # Quiet measurement grid.
    for value in range(80, 433, 44):
        draw.line((80, value, 432, value), fill="#31566b", width=3)
        draw.line((value, 80, value, 432), fill="#31566b", width=3)

    # White reference plane and a warm measured surface profile.
    draw.line((70, 332, 442, 332), fill="#dfe9ef", width=10)
    points = [
        (70, 320),
        (115, 300),
        (160, 268),
        (205, 230),
        (250, 206),
        (295, 218),
        (340, 252),
        (390, 294),
        (442, 314),
    ]
    draw.line(points, fill="#ffb248", width=26, joint="curve")

    # Coordinate axes establish the metrology identity.
    draw.line((94, 414, 94, 104), fill="#f4f7f9", width=14)
    draw.line((94, 414, 420, 414), fill="#f4f7f9", width=14)
    draw.polygon([(94, 80), (77, 116), (111, 116)], fill="#f4f7f9")
    draw.polygon([(444, 414), (408, 397), (408, 431)], fill="#f4f7f9")

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    image.save(PNG_PATH)
    image.save(
        ICON_PATH,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(ICON_PATH)


if __name__ == "__main__":
    main()
