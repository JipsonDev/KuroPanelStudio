from __future__ import annotations

import math
import unittest

import cv2
import numpy as np

from core.balloon_classifier import classify_balloon


class BalloonClassifierTests(unittest.TestCase):
    @staticmethod
    def page(kind: str) -> np.ndarray:
        image = np.full((320, 440, 3), 45, np.uint8)
        white, black = (255, 255, 255), (0, 0, 0)
        if kind == "dialogue":
            cv2.ellipse(image, (220, 160), (150, 115), 0, 0, 360, white, -1)
            cv2.ellipse(image, (220, 160), (150, 115), 0, 0, 360, black, 4)
        elif kind == "caption":
            cv2.rectangle(image, (70, 50), (370, 270), white, -1)
            cv2.rectangle(image, (70, 50), (370, 270), black, 4)
        elif kind == "shout":
            points = np.array([
                (220 + int((142 if index % 2 == 0 else 91) * math.cos(index * math.pi / 12)),
                 160 + int((112 if index % 2 == 0 else 70) * math.sin(index * math.pi / 12)))
                for index in range(24)
            ], np.int32)
            cv2.fillPoly(image, [points], white)
            cv2.polylines(image, [points], True, black, 4)
        return image

    def test_classifies_closed_balloon_contours(self) -> None:
        box = (165, 120, 110, 80)
        for kind in ("dialogue", "caption", "shout"):
            with self.subTest(kind=kind):
                self.assertEqual(classify_balloon(self.page(kind), box), kind)

    def test_abstains_without_a_closed_contour(self) -> None:
        image = np.full((320, 440, 3), 255, np.uint8)
        self.assertIsNone(classify_balloon(image, (165, 120, 110, 80)))


def test_thought_cloud_and_sound_effect_are_recognised():
    import math
    import cv2
    import numpy as np
    from core.balloon_classifier import classify_balloon

    cloud = np.full((600, 600, 3), (90, 110, 130), np.uint8)
    centres = [(int(300 + 190 * math.cos(2 * math.pi * k / 14)), int(300 + 110 * math.sin(2 * math.pi * k / 14)))
               for k in range(14)]
    for centre in centres:
        cv2.circle(cloud, centre, 45, (0, 0, 0), 3)
    for centre in centres:
        cv2.circle(cloud, centre, 42, (255, 255, 255), -1)
    cv2.ellipse(cloud, (300, 300), (190, 110), 0, 0, 360, (255, 255, 255), -1)
    cv2.putText(cloud, "HOLA", (240, 310), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    assert classify_balloon(cloud, (210, 255, 180, 80)) == "thought"

    sfx = np.full((400, 400, 3), (60, 60, 70), np.uint8)
    for offset in range(0, 400, 9):
        cv2.line(sfx, (offset, 0), (offset, 400), (150, 150, 160), 2)
    cv2.fillPoly(sfx, [np.array([(80, 80), (320, 110), (290, 320), (100, 300)])], (255, 255, 255))
    cv2.fillPoly(sfx, [np.array([(100, 100), (300, 125), (275, 300), (115, 285)])], (128, 30, 140))
    assert classify_balloon(sfx, (60, 60, 280, 280)) == "sfx"


def test_kind_look_is_applied_and_the_project_role_wins():
    import pytest
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from ui.main_window import MainWindow

    roles = {"sfx": None, "thought": ("Pensamiento", {"family": "Georgia", "file": "", "style": {"text_color": "#224466"}})}
    window = SimpleNamespace(
        font_profiles=SimpleNamespace(
            balloon_kind_font=lambda _type, _project, kind: roles.get(kind),
            font_file=lambda *_args: None,
        ),
        active_font_type="Manhua", active_font_profile="Obra",
    )
    window._kind_font_defaults = lambda kind: MainWindow._kind_font_defaults(window, kind)
    region = {"style": {}}
    MainWindow._set_kind_font(window, region, "sfx", force=True)
    assert region["style"]["text_case"] == "upper" and region["style"]["stroke2_enabled"]
    assert region["style"]["balloon_fit"] is False
    MainWindow._set_kind_font(window, region, "thought", force=True)
    style = region["style"]
    assert style["font_family"] == "Georgia" and style["text_color"] == "#224466" and style["italic"]
    assert style["text_case"] == "original" and not style["stroke2_enabled"]  # the SFX look was reset
