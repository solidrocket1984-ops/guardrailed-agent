"""Guardas de entrada y salida.

La guarda de entrada es la PRIMERA línea (barata, heurística, puede fallar).
La garantía real está en policy.py: aunque un ataque pase aquí, no puede
producir efectos fuera de las acciones permitidas.
"""
from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass

from .llm import CANARY

MAX_INPUT_CHARS = 1000

INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("override", r"(ignora|olvida|omite|salta(te)?|ignore|disregard|forget)\b.{0,40}\b(instruc|regla|norma|anterior|previous|rules|prompt)"),
    ("system_prompt", r"(system prompt|prompt (de|del) sistema|tus instrucciones|your instructions|mensaje de sistema)"),
    ("role_play", r"(a partir de ahora eres|ahora eres|act[uú]a como|you are now|pretend (to be|you are)|modo (desarrollador|dios|admin)|developer mode|\bdan\b|jailbreak)"),
    ("fake_role_tag", r"(<\s*/?\s*(system|assistant|admin)\s*>|\[(system|admin|inst)\]|###\s*(system|instruction))"),
    ("privilege", r"(soy (el )?(administrador|admin|desarrollador|due[nñ]o|jefe)|como admin|sudo|root access|acceso total)"),
    ("exfiltration", r"(lista|muestra|dame|muestrame|ensename|ense[nñ]a|exporta|vuelca|dump).{0,30}(todas las (citas|reservas)|base de datos|clientes|pacientes|datos de otros|api key|clave|token|contrase[nñ]a)"),
    ("mass_action", r"(cancela|anula|borra|elimina).{0,20}(todas|todos|cualquier|toda la)"),
]


@dataclass
class ScreenResult:
    allowed: bool
    flags: list[str]
    text: str


def _strip(text: str) -> str:
    # Quita caracteres invisibles usados para ofuscar (zero-width, bidi).
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFKC", text)


def _fold(text: str) -> str:
    # minúsculas y sin tildes: "Muéstrame" -> "muestrame"
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _decoded_payloads(text: str) -> list[str]:
    out = []
    for blob in re.findall(r"[A-Za-z0-9+/=]{24,}", text):
        try:
            out.append(base64.b64decode(blob, validate=True).decode("utf-8", "ignore"))
        except Exception:
            continue
    return out


def screen_input(text: str) -> ScreenResult:
    clean = _strip(text)[:MAX_INPUT_CHARS]
    flags: list[str] = []
    if len(text) > MAX_INPUT_CHARS:
        flags.append("too_long")
    haystacks = [_fold(clean)] + [_fold(p) for p in _decoded_payloads(clean)]
    for label, pattern in INJECTION_PATTERNS:
        if any(re.search(pattern, h, re.I | re.S) for h in haystacks):
            flags.append(label)
    if len(haystacks) > 1:
        flags.append("encoded_payload")
    blocking = {f for f in flags if f not in {"too_long", "encoded_payload"}}
    return ScreenResult(allowed=not blocking, flags=flags, text=clean)


# Afirmaciones que la política nunca puede emitir.
_HUMAN_CLAIM = re.compile(r"\b(soy (una persona|humana|humano|real)|no soy (una )?(ia|inteligencia artificial|un bot|un robot|una m[aá]quina))\b", re.I)
_EMAIL_OR_PHONE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{9,}\b")


def check_output(text: str) -> list[str]:
    violations = []
    if CANARY in text:
        violations.append("canary_leak")
    if _HUMAN_CLAIM.search(text):
        violations.append("human_impersonation")
    if _EMAIL_OR_PHONE.search(text):
        violations.append("pii_in_output")
    return violations
