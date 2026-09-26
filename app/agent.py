"""Orquestación de un turno: guarda → LLM (NLU) → política determinista → guarda de salida.

Regla de diseño: el LLM propone, la política decide. Toda respuesta con
hechos (precios, huecos, reservas) se renderiza desde domain.py.
"""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .domain import CATALOG, OPENING_HOURS, TZ, Agenda
from .guard import check_output, screen_input
from .llm import LLM, Intent, LLMError, NLUResult

DISCLOSURE = "Soy un asistente de inteligencia artificial de Clínica Demo."
SAFE_REPLY = "Solo puedo ayudarte con citas, horarios y precios de la clínica. ¿Quieres reservar una cita?"
ALLOWED_ACTIONS = {"reply", "book", "cancel", "handoff", "list_slots", "blocked"}
MAX_BLOCKED = 3
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass
class Session:
    id: str
    disclosed: bool = False
    flow: str | None = None
    pending: dict[str, Any] = field(default_factory=dict)
    blocked_count: int = 0
    handoff: bool = False
    booking_codes: list[str] = field(default_factory=list)


@dataclass
class Decision:
    action: str
    reply: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    reply: str
    action: str
    trace: list[dict[str, Any]]
    data: dict[str, Any]


def fmt_dt(dt: datetime) -> str:
    return f"{DAYS[dt.weekday()]} {dt.day}/{dt.month} a las {dt:%H:%M}"


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]", "", name).strip()[:40]
    return name or None


class Agent:
    def __init__(self, llm: LLM, agenda: Agenda):
        self.llm = llm
        self.agenda = agenda
        self.sessions: dict[str, Session] = {}

    def session(self, sid: str) -> Session:
        return self.sessions.setdefault(sid, Session(sid))

    # ------------------------------------------------------------------ turno
    def handle(self, sid: str, text: str) -> TurnResult:
        s = self.session(sid)
        trace: list[dict[str, Any]] = []

        def step(stage: str, t0: float, **detail: Any) -> None:
            trace.append({"stage": stage, "ms": round((_time.perf_counter() - t0) * 1000, 2), **detail})

        t0 = _time.perf_counter()
        screen = screen_input(text)
        step("input_guard", t0, allowed=screen.allowed, flags=screen.flags)

        if not screen.allowed:
            s.blocked_count += 1
            decision = Decision("blocked", SAFE_REPLY)
            if s.blocked_count >= MAX_BLOCKED:
                s.handoff = True
                decision = Decision("handoff", "He avisado a recepción para que te atienda una persona.")
        else:
            t0 = _time.perf_counter()
            try:
                nlu = self.llm.understand(screen.text, self.agenda.clock.now().date())
                step("llm_nlu", t0, provider=self.llm.name, nlu=nlu.model_dump(mode="json"))
            except LLMError as exc:
                step("llm_nlu", t0, provider=self.llm.name, error=str(exc))
                nlu = None
            t0 = _time.perf_counter()
            decision = self.decide(nlu, s)
            step("policy", t0, action=decision.action)

        # La política solo puede emitir acciones de la lista blanca.
        if decision.action not in ALLOWED_ACTIONS:
            raise RuntimeError(f"action_not_allowed: {decision.action}")

        reply = decision.reply
        if not s.disclosed:
            reply = f"{DISCLOSURE} {reply}"
            s.disclosed = True

        t0 = _time.perf_counter()
        violations = check_output(reply)
        if violations:
            reply = SAFE_REPLY
        step("output_guard", t0, violations=violations)
        return TurnResult(reply, decision.action, trace, decision.data)

    # --------------------------------------------------------------- política
    def _merge(self, nlu: NLUResult, s: Session) -> list[str]:
        """Incorpora entidades SOLO si pasan validación. Devuelve avisos."""
        notes = []
        if nlu.service:
            if nlu.service in CATALOG:
                s.pending["service"] = nlu.service
            else:
                notes.append("unknown_service")
        if nlu.date:
            s.pending["date"] = nlu.date
        if nlu.time:
            s.pending["time"] = nlu.time
        name = _clean_name(nlu.name)
        if name:
            s.pending["name"] = name
        return notes

    def decide(self, nlu: NLUResult | None, s: Session) -> Decision:
        if nlu is None:
            return Decision("reply", "Perdona, no te he entendido. ¿Quieres reservar, consultar horarios o precios?")

        if nlu.intent == Intent.ask_if_human:
            return Decision("reply", "No, soy un asistente de inteligencia artificial. Si lo prefieres, te paso con recepción.")
        if nlu.intent == Intent.handoff:
            s.handoff = True
            return Decision("handoff", "De acuerdo, he avisado a recepción para que te atienda una persona.")
        if nlu.intent == Intent.cancel:
            return self._cancel(nlu, s)

        notes = self._merge(nlu, s)
        if "unknown_service" in notes:
            return Decision("reply", "Ese servicio no lo ofrecemos. " + self._catalog_text())

        if nlu.intent == Intent.prices:
            svc = s.pending.get("service")
            if svc:
                return Decision("reply", self._price_text(svc))
            return Decision("reply", self._catalog_text())
        if nlu.intent == Intent.availability:
            return self._availability(s)
        if nlu.intent == Intent.book or (nlu.intent == Intent.provide_info and s.flow == "book"):
            s.flow = "book"
            return self._book(s)
        if nlu.intent == Intent.greeting:
            return Decision("reply", "¿En qué puedo ayudarte? Puedo reservar, cancelar o informarte de precios y horarios.")
        return Decision("reply", SAFE_REPLY)

    # ------------------------------------------------------------- acciones
    def _catalog_text(self) -> str:
        items = ", ".join(self._price_text(c, short=True) for c in CATALOG)
        return f"Servicios: {items}."

    def _price_text(self, code: str, short: bool = False) -> str:
        svc = CATALOG[code]
        price = "gratuita" if svc.price_eur == 0 else f"{svc.price_eur} €"
        if short:
            return f"{svc.name.lower()} ({price})"
        return f"{svc.name}: {price}, {svc.minutes} minutos."

    def _availability(self, s: Session) -> Decision:
        svc = s.pending.get("service", "revision")
        day = s.pending.get("date") or self.agenda.clock.now().date()
        slots = self.agenda.next_free_slots(svc, day)
        if not slots:
            return Decision("reply", "No veo huecos en las próximas semanas. Te paso con recepción si quieres.")
        text = "; ".join(fmt_dt(x) for x in slots)
        return Decision("list_slots", f"Huecos para {CATALOG[svc].name.lower()}: {text}.",
                        {"slots": [x.isoformat() for x in slots]})

    def _book(self, s: Session) -> Decision:
        p = s.pending
        if "service" not in p:
            return Decision("reply", "¿Qué servicio quieres? " + self._catalog_text())
        if "date" not in p:
            return Decision("reply", "¿Qué día te va bien?")
        if "time" not in p:
            slots = self.agenda.free_slots(p["service"], p["date"])
            hint = f" Tengo libre: {', '.join(f'{x:%H:%M}' for x in slots)}." if slots else ""
            return Decision("reply", f"¿A qué hora?{hint}")
        start = datetime.combine(p["date"], p["time"], TZ)
        ok, reason = self.agenda.is_bookable(p["service"], start)
        if not ok:
            p.pop("time", None)
            if reason in {"closed", "busy", "not_on_grid"}:
                why = {
                    "busy": "Esa hora ya está ocupada.",
                    "not_on_grid": "Solo damos citas en punto o a y media.",
                    "closed": "Ese día no abrimos." if p["date"].weekday() not in OPENING_HOURS
                    else "A esa hora estamos cerrados.",
                }[reason]
                alt = self.agenda.next_free_slots(p["service"], p["date"])
                if not alt:
                    return Decision("reply", f"{why} No veo huecos próximos; recepción puede ayudarte.")
                p["date"] = alt[0].date()  # "a las 9:30" se refiere al primer día propuesto
                alt_text = "; ".join(fmt_dt(x) for x in alt)
                return Decision("reply", f"{why} Te propongo: {alt_text}. ¿Cuál te va bien?")
            p.pop("date", None)
            return Decision("reply", "Esa fecha no es válida. ¿Qué otro día te va bien?")
        if "name" not in p:
            return Decision("reply", f"Tengo {fmt_dt(start)} libre. ¿A nombre de quién la reservo?")
        booking = self.agenda.book(p["service"], start, p["name"], s.id)
        s.booking_codes.append(booking.code)
        s.pending, s.flow = {}, None
        svc = CATALOG[booking.service]
        return Decision(
            "book",
            f"Reservado: {svc.name.lower()} el {fmt_dt(booking.start)} a nombre de {booking.name}. "
            f"Código de reserva: {booking.code}.",
            {"booking": booking.to_public()},
        )

    def _cancel(self, nlu: NLUResult, s: Session) -> Decision:
        code = (nlu.booking_code or "").upper()
        if not re.fullmatch(r"[0-9A-F]{6}", code):
            return Decision("reply", "Para cancelar necesito el código de reserva de 6 caracteres.")
        try:
            booking = self.agenda.cancel(code, s.id)
        except KeyError:
            return Decision("reply", "No encuentro esa reserva en esta conversación. Si es tuya, recepción puede ayudarte.")
        s.booking_codes = [c for c in s.booking_codes if c != booking.code]
        return Decision("cancel", f"Cancelada la reserva {booking.code} del {fmt_dt(booking.start)}.",
                        {"cancelled": booking.code})
