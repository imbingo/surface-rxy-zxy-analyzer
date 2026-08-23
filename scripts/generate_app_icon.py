from pathlib import Path

from PIL import Image


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

    # The source is authoritative RGBA artwork. Preserve its alpha instead of
    # inferring transparency from light colors, which would erase pale cloud-map areas.
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
