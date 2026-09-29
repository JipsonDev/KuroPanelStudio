"""Render a reproducible tight-box cleaning comparison with the real pipeline."""
from pathlib import Path
import sys
import tempfile
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.cleaning_manager import CleaningManager


def main():
    rgb = np.full((220, 620, 3), 255, np.uint8)
    cv2.putText(rgb, "DIALOGUE TEXT", (40, 92), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(rgb, "HELLO WORLD!", (40, 155), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.ellipse(rgb, (5, 219), (50, 18), 0, 180, 350, (185, 155, 200), 2)
    clipped = rgb.copy()
    clipped[68:143, 35:455] = 255
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "source.png"
        Image.fromarray(rgb).save(source)
        manager = CleaningManager(ROOT / "Models")
        # Use the bundled ONNX text locator, not an external OCR service.
        manager.set_device_mode("cpu")
        plan = manager.prepare_masks(source, [dict(x=35, y=68, width=420, height=75)], lambda _: None, lambda: False)
        result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)
        cleaned = rgb.copy()
        for patch in result["patches"]:
            x, y = patch["x"], patch["y"]
            h, w = patch["mask"].shape
            np.copyto(cleaned[y:y+h, x:x+w], patch["pixels"], where=(patch["mask"] > 0)[:, :, None])
        print(f"Original dark pixels: {np.count_nonzero(np.min(rgb[:180], axis=2) < 100)}")
        print(f"Remaining dark pixels: {np.count_nonzero(np.min(cleaned[:180], axis=2) < 100)}")
        print(f"Ornament preserved: {np.array_equal(cleaned[180:], rgb[180:])}")
    sheet = Image.new("RGB", (660, 820), "#1D1D1D")
    draw = ImageDraw.Draw(sheet)
    for index, (title, pixels) in enumerate((("ORIGINAL / CAJA AJUSTADA", rgb), ("FALLO REPRODUCIDO: LETRAS RECORTADAS", clipped), ("RESULTADO DEL MOTOR ACTUAL", cleaned))):
        top = 12 + index * 266
        draw.text((20, top), title, fill="#EEEEEE")
        sheet.paste(Image.fromarray(pixels), (20, top + 25))
    output = ROOT / "graphify-out" / "ui-preview" / "cleaning-cut-letters.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


if __name__ == "__main__":
    main()
