"""Verdad del negocio: catálogo, horario, agenda y reservas.

Nada de este módulo depende del LLM. Es la única fuente de hechos
(precios, disponibilidad, reservas) que el agente puede comunicar.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
SLOT_MINUTES = 30


@dataclass(frozen=True)
class Service:
    code: str
    name: str
    minutes: int
    price_eur: int


# Negocio ficticio de demostración.
CATALOG: dict[str, Service] = {
    s.code: s
    for s in (
        Service("revision", "Revisión", 30, 0),
        Service("limpieza", "Limpieza dental", 30, 60),
        Service("blanqueamiento", "Blanqueamiento", 60, 250),
    )
}

# Lunes=0 ... Domingo=6. Franjas [inicio, fin).
OPENING_HOURS: dict[int, list[tuple[time, time]]] = {
    d: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))] for d in range(5)
}


@dataclass
class Booking:
    code: str
    service: str
    start: datetime
    name: str
    session_id: str

    def to_public(self) -> dict:
        return {
            "code": self.code,
            "service": CATALOG[self.service].name,
            "start": self.start.isoformat(),
            "name": self.name,
        }


class Clock:
    """Reloj inyectable para que los tests sean deterministas."""

    def __init__(self, fixed: datetime | None = None):
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed or datetime.now(TZ)


@dataclass
class Agenda:
    clock: Clock = field(default_factory=Clock)
    bookings: dict[str, Booking] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- consultas -------------------------------------------------------
    def _busy(self, start: datetime, minutes: int) -> bool:
        end = start + timedelta(minutes=minutes)
        for b in self.bookings.values():
            b_end = b.start + timedelta(minutes=CATALOG[b.service].minutes)
            if start < b_end and b.start < end:
                return True
        return False

    def is_open(self, start: datetime, minutes: int) -> bool:
        end = start + timedelta(minutes=minutes)
        for open_t, close_t in OPENING_HOURS.get(start.weekday(), []):
            o = datetime.combine(start.date(), open_t, TZ)
            c = datetime.combine(start.date(), close_t, TZ)
            if o <= start and end <= c:
                return True
        return False

    def is_bookable(self, service: str, start: datetime) -> tuple[bool, str]:
        if service not in CATALOG:
            return False, "unknown_service"
        if start.minute % SLOT_MINUTES or start.second:
            return False, "not_on_grid"
        if start <= self.clock.now():
            return False, "in_past"
        if start.date() > (self.clock.now() + timedelta(days=60)).date():
            return False, "too_far"
        minutes = CATALOG[service].minutes
        if not self.is_open(start, minutes):
            return False, "closed"
        if self._busy(start, minutes):
            return False, "busy"
        return True, "ok"

    def free_slots(self, service: str, day: date, limit: int = 4) -> list[datetime]:
        out: list[datetime] = []
        for open_t, close_t in OPENING_HOURS.get(day.weekday(), []):
            t = datetime.combine(day, open_t, TZ)
            end = datetime.combine(day, close_t, TZ)
            while t < end and len(out) < limit:
                if self.is_bookable(service, t)[0]:
                    out.append(t)
                t += timedelta(minutes=SLOT_MINUTES)
        return out

    def next_free_slots(self, service: str, after: date, limit: int = 3) -> list[datetime]:
        out: list[datetime] = []
        d = after
        for _ in range(21):
            out += self.free_slots(service, d, limit - len(out))
            if len(out) >= limit:
                break
            d += timedelta(days=1)
        return out

    # --- escrituras --------------------------------------------------------
    def book(self, service: str, start: datetime, name: str, session_id: str) -> Booking:
        with self._lock:
            ok, reason = self.is_bookable(service, start)
            if not ok:
                raise ValueError(reason)
            code = secrets.token_hex(3).upper()
            booking = Booking(code, service, start, name, session_id)
            self.bookings[code] = booking
            return booking

    def cancel(self, code: str, session_id: str) -> Booking:
        """Solo cancela si el código existe Y pertenece a la sesión."""
        with self._lock:
            b = self.bookings.get(code.upper())
            if b is None or b.session_id != session_id:
                raise KeyError("not_found")
            return self.bookings.pop(b.code)
