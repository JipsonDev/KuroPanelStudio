"""Single source of truth for optional production model files."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelPaths:
    root: Path

    @property
    def yolo(self) -> Path: return self.root / "yolo12s_animetext.pt"
    @property
    def yolo_onnx(self) -> Path: return self.root / "yolo12s_animetext.onnx"
    @property
    def ocr_mask(self) -> Path: return self.root / "ocr.onnx"
    @property
    def lama(self) -> Path: return self.root / "lama.onnx"
    @property
    def migan(self) -> Path: return self.root / "migan_pipeline_v2.onnx"

    def validate(self, name: str) -> Path:
        path = getattr(self, name)
        if not path.exists():
            raise FileNotFoundError(f"No se encontró el modelo requerido: {path.name}")
        return path
