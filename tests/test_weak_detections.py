import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.detection_manager import DetectionManager, TextRegion


def test_outlined_sound_effect_is_kept_and_artwork_is_not():
    image = Image.new("RGB", (400, 400), (60, 60, 70))
    draw = ImageDraw.Draw(image)
    for offset in range(0, 400, 9):  # speed lines behind the effect
        draw.line((offset, 0, offset, 400), fill=(150, 150, 160), width=2)
    draw.polygon([(80, 80), (320, 110), (290, 320), (100, 300)], fill="white")
    draw.polygon([(100, 100), (300, 125), (275, 300), (115, 285)], fill=(128, 30, 140))
    box = TextRegion(60, 60, 280, 280, 0.2)
    assert DetectionManager._weak_text_evidence(image, box)

    rng = np.random.default_rng(3)
    art = Image.fromarray(rng.integers(0, 255, (300, 300, 3), dtype=np.uint8))
    assert not DetectionManager._weak_text_evidence(art, TextRegion(0, 0, 300, 300, 0.2))
    # A thin outlined watermark line is not a sound effect.
    thin = image.crop((0, 0, 400, 400))
    assert not DetectionManager._weak_text_evidence(thin, TextRegion(60, 150, 280, 40, 0.2))


def test_narration_text_on_flat_ground_is_kept():
    image = Image.new("RGB", (600, 900), (238, 226, 200))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    for line in range(6):
        draw.text((30, 24 + line * 38), "LAS ARPÍAS SON UNA RAZA CELOSA", fill="black", font=font)
    assert DetectionManager._weak_text_evidence(image, TextRegion(20, 20, 560, 240, 0.2))
