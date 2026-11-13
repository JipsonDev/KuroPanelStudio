"""Turn raw processing errors into one clear message: what failed, why, what to do.

Workers raise exceptions written for logs. The interface used to repeat that
text in the progress bar, a toast and the provider status. Every place now
shows the same short headline and action, and keeps the original text as the
technical detail.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Failure:
    kind: str
    title: str
    action: str
    detail: str
    retryable: bool = False

    @property
    def summary(self) -> str:
        return f"{self.title}. {self.action}"


_PROVIDER_PREFIX = re.compile(r"^(?:Alibaba Cloud|DashScope|Qwen)\s*(?:\(HTTP \d+\))?\s*:\s*", re.IGNORECASE)

# (kind, title, action, retryable, fragments). The first match wins, so the
# specific billing and credential rules come before generic HTTP failures.
_RULES = (
    ("cancelled", "Operación cancelada", "No se aplicó ningún resultado.", False,
     ("operación cancelada",)),
    ("billing", "Sin saldo o cuota en el proveedor",
     "Recarga saldo o revisa la cuota del modelo en Alibaba Cloud Model Studio.", False,
     ("saldo insuficiente", "insufficient balance", "cuota gratuita", "free quota", "http 402")),
    ("credentials", "Clave de API no válida",
     "Revisa la clave y la región en Configuración → OCR y traducción.", False,
     ("clave no válida", "invalid_api_key", "incorrect api key", "http 401", "http 403", "agrega tu api key")),
    ("model", "Modelo o dirección del proveedor no disponible",
     "Comprueba el nombre del modelo y la URL de tu región en Configuración → OCR y traducción.", False,
     ("model_not_found", "modelo o ruta", "http 404", "la url ocr", "nombre del modelo")),
    ("rate_limit", "El proveedor está saturado",
     "Espera unos segundos y vuelve a ejecutar; solo se enviarán las cajas pendientes.", True,
     ("límite de solicitudes", "rate limit", "too many requests", "http 429", "http 503")),
    ("network", "Se cortó la conexión con el proveedor",
     "Revisa tu conexión y vuelve a ejecutar; las cajas ya leídas no se vuelven a pagar.", True,
     ("10054", "10053", "10060", "conexión", "connection", "timed out", "timeout", "remote host",
      "interrumpió", "http 500", "http 502", "http 504", "name resolution", "proxy")),
    ("truncated", "La lectura quedó incompleta",
     "Divide la caja en selecciones más pequeñas y vuelve a leerla.", False,
     ("límite de salida", "cortó la lectura")),
    ("memory", "Memoria insuficiente",
     "Cierra otras aplicaciones o elige el perfil de rendimiento Bajo en Configuración.", True,
     ("memoryerror", "out of memory", "bad_alloc", "not enough memory", "failed to allocate")),
    ("gpu", "La GPU no está disponible",
     "Elige CPU o Automático en Configuración → Rendimiento.", False,
     ("modo gpu", "cuda", "directml")),
    ("model_files", "Falta un modelo local",
     "Reinstala KuroPanel Studio para recuperar los modelos.", False,
     ("no se encontró el modelo requerido",)),
    ("busy_file", "Photoshop todavía está guardando el archivo",
     "Espera a que termine de guardar y vuelve a intentarlo.", True,
     ("photoshop todavía está escribiendo", "versión estable de")),
    ("missing_file", "No se encuentra la imagen",
     "Comprueba que la carpeta del capítulo no se haya movido o renombrado.", False,
     ("ya no existe", "no such file", "filenotfound", "no se pudo abrir")),
    ("no_boxes", "No hay cajas de texto",
     "Ejecuta primero Detectar cajas o dibuja las cajas a mano.", False,
     ("autodetectar texto", "no hay regiones")),
    ("newer_project", "El proyecto es de una versión más reciente",
     "Actualiza KuroPanel Studio para abrirlo.", False,
     ("versión más reciente del editor",)),
)


def describe_failure(operation: str, message: str) -> Failure:
    """Classify an error message; unknown errors keep their own first line."""
    detail = str(message or "").strip()
    lowered = detail.casefold()
    for kind, title, action, retryable, fragments in _RULES:
        if any(fragment in lowered for fragment in fragments):
            return Failure(kind, title, action, detail, retryable)
    first_line = _PROVIDER_PREFIX.sub("", detail.splitlines()[0] if detail else "").strip().rstrip(".")
    name = str(operation or "").strip()
    title = first_line or (f"{name} no se completó" if name else "El proceso no se completó")
    return Failure(
        "unknown", title[:140],
        "Vuelve a intentarlo. Si se repite, abre «Ver detalle» y copia el texto al informar del error.",
        detail, True,
    )


def format_remaining(elapsed: float, percent: float) -> str:
    """Estimate the remaining time once progress is steady enough to trust."""
    if percent < 5 or percent >= 100 or elapsed < 3:
        return ""
    remaining = elapsed * (100 - percent) / percent
    if remaining < 5:
        return "casi listo"
    if remaining < 60:
        return f"quedan ~{int(round(remaining / 5) * 5)} s"
    minutes = int(round(remaining / 60))
    return f"quedan ~{minutes} min"
