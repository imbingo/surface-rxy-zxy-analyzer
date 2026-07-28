from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
SOURCE_PATH = ASSET_DIR / "SurfaceRxyZxyAnalyzer.logo-source.png"
ICON_PATH = ASSET_DIR / "SurfaceRxyZxyAnalyzer.ico"
PNG_PATH = ASSET_DIR / "SurfaceRxyZxyAnalyzer.png"


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"Missing logo source: {SOURCE_PATH}")

    source = Image.open(SOURCE_PATH).convert("RGBA")
    side = min(source.size)
    left = (source.width - side) // 2
    top = (source.height - side) // 2
    source = source.crop((left, top, left + side, top + side))

    # Recreate the generated tile boundary as alpha for clean Windows corners.
    mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)
    inset = max(4, round(side * 0.006))
    radius = round(side * 0.16)
    draw.rounded_rectangle(
        (inset, inset, side - inset - 1, side - inset - 1),
        radius=radius,
        fill=255,
    )
    source_pixels = source.load()
    mask_pixels = mask.load()
    for y in range(side):
        for x in range(side):
            red, green, blue, _ = source_pixels[x, y]
            if red >= 220 and green >= 220 and blue >= 220:
                mask_pixels[x, y] = 0
    source.putalpha(mask)
    image = source.resize((512, 512), Image.Resampling.LANCZOS)

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    image.save(PNG_PATH, optimize=True)
    image.save(
        ICON_PATH,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(ICON_PATH)


if __name__ == "__main__":
    main()
