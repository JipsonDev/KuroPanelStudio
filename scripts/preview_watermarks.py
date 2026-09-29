"""Render synthetic distribution examples and the real Qt settings dialog."""
from pathlib import Path
from io import BytesIO
import os
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from core.watermark_manager import compose_watermark, watermark_positions


def main():
    output = ROOT / "graphify-out/ui-preview"
    output.mkdir(parents=True, exist_ok=True)
    logo = Image.new("RGBA", (160, 48), (0, 0, 0, 0))
    painter = ImageDraw.Draw(logo)
    painter.rounded_rectangle((0, 0, 159, 47), radius=9, fill="#242424")
    painter.text((16, 11), "KUROPANEL", font=ImageFont.load_default(size=21), fill="white")
    payload = BytesIO()
    logo.save(payload, "PNG")
    base = dict(png_bytes=payload.getvalue(), size_mode="pixels", width_px=160,
                opacity=85, repeat=True, auto_count=False, repeat_count=5,
                minimum_gap=100, seam_safe=True, anchor="top-right", margin_x=32)
    sheet = Image.new("RGB", (820, 950), "#1D1D1D")
    draw = ImageDraw.Draw(sheet)
    for index, (title, changes) in enumerate([
        ("COLUMNA ALINEADA", {}),
        ("ALTERNAR LADOS", dict(distribution="alternating")),
        ("RESPETAR TEXTO", dict(avoid_regions=[dict(x=530, y=0, width=270, height=950)])),
        ("20 SOLICITADAS / ESPACIO LIMITADO", dict(repeat_count=20, minimum_gap=180)),
    ]):
        settings = {**base, **changes}
        page = Image.new("RGB", (800, 1600), "#D3D0CA")
        pd = ImageDraw.Draw(page)
        for y in (40, 560, 1080):
            pd.rectangle((26, y, 774, y + 440), fill="#B5B3AE")
        for region in settings.get("avoid_regions", []):
            x, y, w, h = (region[k] for k in ("x", "y", "width", "height"))
            pd.rectangle((x, y, x+w-1, y+h-1), fill="#F5F2EC", outline="#8C6759", width=4)
            pd.text((x+20, y+80), "TEXTO", font=ImageFont.load_default(size=30), fill="#444444")
        compose_watermark(page, settings)
        count = len(watermark_positions(page.size, logo.size, settings))
        print(f"{title}: {count} marks")
        x, y = 20 + (index % 2) * 400, 16 + (index // 2) * 470
        draw.text((x, y), title, font=ImageFont.load_default(size=16), fill="#EEEEEE")
        draw.text((x, y+23), f"{count} marcas visibles", fill="#BBBBBB")
        sheet.paste(page.resize((200, 400), Image.Resampling.LANCZOS), (x+80, y+48))
    sheet.save(output / "watermark-distribution.png")

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from ui.watermark_dialog import WatermarkDialog
    app = QApplication.instance() or QApplication([])
    # Qt offscreen on Windows needs explicit system font registration.
    font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
        if (font_root / name).exists():
            QFontDatabase.addApplicationFont(str(font_root / name))
    app.setStyleSheet((ROOT / "assets/styles/dark_theme.qss").read_text(encoding="utf-8"))
    dialog = WatermarkDialog({**base, "distribution": "alternating", "source_name": "KuroPanel.png"})
    dialog.resize(480, 880)
    dialog.set_scope_status(True, 1, 1, 5)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / "watermark-dialog.png"))
    dialog.close()


if __name__ == "__main__":
    main()
