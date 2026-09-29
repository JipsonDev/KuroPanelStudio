"""Compare per-page distribution with a continuous chapter using synthetic data."""
from pathlib import Path
from io import BytesIO
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from core.chapter_watermarks import chapter_watermark_settings
from core.watermark_manager import compose_watermark, watermark_positions


def main():
    mark = Image.new("RGBA", (140, 60), "#303030")
    ImageDraw.Draw(mark).text((14, 20), "KUROPANEL", fill="white", font=ImageFont.load_default(size=18))
    payload = BytesIO()
    mark.save(payload, "PNG")
    pages = [dict(name=f"{i}.png", width=800, height=1000) for i in range(3)]
    settings = dict(png_bytes=payload.getvalue(), size_mode="pixels", width_px=140,
                    repeat=True, auto_count=False, repeat_count=4, minimum_gap=500,
                    opacity=100, enabled_pages=[p["name"] for p in pages])
    plan = chapter_watermark_settings(pages, settings)
    sheet = Image.new("RGB", (690, 1260), "#202020")
    draw = ImageDraw.Draw(sheet)
    for column, label in enumerate(("ANTES: CADA PAGINA", "AHORA: CAPITULO CONTINUO")):
        x = 20 + column * 335
        draw.text((x, 15), label, fill="white", font=ImageFont.load_default(size=17))
        global_points = []
        for i, page in enumerate(pages):
            image = Image.new("RGB", (800, 1000), "#E1DDD7" if i % 2 == 0 else "#C4C0B9")
            pd = ImageDraw.Draw(image)
            pd.text((30, 35), f"Pagina {i + 1}", fill="#555555", font=ImageFont.load_default(size=30))
            config = settings if column == 0 else plan[page["name"]]
            compose_watermark(image, config)
            for _, y in watermark_positions(image.size, mark.size, config):
                global_points.append(i * 1000 + y)
            sheet.paste(image.resize((312, 390), Image.Resampling.LANCZOS), (x, 62 + i * 390))
            if i:
                draw.line((x, 62 + i * 390, x+312, 62 + i * 390), fill="#B77C50", width=2)
        gap = min(b - a - mark.height for a, b in zip(global_points, global_points[1:]))
        draw.text((x, 39), f"{len(global_points)} marcas / menor separacion: {gap}px", fill="#CCCCCC")
        print(f"{label}: {len(global_points)} marks, minimum edge-to-edge gap {gap}px")
    output = ROOT / "graphify-out/ui-preview/chapter-watermarks.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


if __name__ == "__main__":
    main()
