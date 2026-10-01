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
