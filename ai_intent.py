# ai_intent.py
# Detector simples de "fechamento" + extração de local/horário/pagamento.
# Regras baseadas em padrões comuns (PT-BR). Não usa IA aqui — é rápido e barato.

import re
from datetime import datetime, timedelta
from typing import Dict, Optional

HOUR_PATTERNS = [
    r"\b(\d{1,2})\s?h\b",                 # "20h", "8 h"
    r"\bàs?\s*(\d{1,2})(?::(\d{2}))?\b",  # "às 20", "as 20:30"
    r"\bpor volta (?:de|das)\s*(\d{1,2})\b",
]
DATE_PATTERNS = [
    r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b",  # "21/08", "21/08/2025"
    r"\bamanhã\b",
    r"\bhoje\b",
]

PLACE_KEYWORDS = {
    "meu_local": ["meu local", "no meu local", "minha casa", "no villa rosa", "villa rosa"],
    "motel": ["motel"],
    "casa_cliente": ["seu ap", "seu apê", "sua casa", "no seu ap", "no seu ape", "no seu apto", "no seu apartamento"],
}

PAYMENT_KEYWORDS = {
    "pix": ["pix", "chave pix"],
    "dinheiro": ["dinheiro", "em cash", "em espécie", "especie"],
    "cartao": ["cartão", "cartao", "débito", "debito", "crédito", "credito", "maquininha"],
}

CLOSE_VERBS = [
    "fechar", "marcar", "combinar", "agendar", "tá combinado", "esta combinado",
    "tá fechado", "está fechado", "fechado", "vamos fechar", "vamos marcar",
]

NEGATIVE_CANCEL = [
    "cancel", "desmarca", "desmarcar", "não quero", "nao quero", "deixa pra lá", "deixa pra la"
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _find_place(txt: str) -> Optional[str]:
    t = _norm(txt)
    for key, words in PLACE_KEYWORDS.items():
        for w in words:
            if w in t:
                return key
    return None


def _find_payment(txt: str) -> Optional[str]:
    t = _norm(txt)
    for key, words in PAYMENT_KEYWORDS.items():
        for w in words:
            if w in t:
                return key
    return None


def _find_time(txt: str) -> Optional[str]:
    t = _norm(txt)
    # horas explícitas
    for pat in HOUR_PATTERNS:
        m = re.search(pat, t)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2)) if m.lastindex and m.group(m.lastindex) else 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"
    return None


def _find_date(txt: str) -> Optional[str]:
    t = _norm(txt)
    # hoje/amanhã
    if "hoje" in t:
        return datetime.now().date().isoformat()
    if "amanhã" in t or "amanha" in t:
        return (datetime.now() + timedelta(days=1)).date().isoformat()

    # dd/mm(/aaaa)
    for pat in DATE_PATTERNS:
        m = re.search(pat, t)
        if m and m.lastindex and m.lastindex >= 2:
            dd = int(m.group(1))
            mm = int(m.group(2))
            yyyy = int(m.group(3)) if (m.lastindex >= 3 and m.group(3)) else datetime.now().year
            try:
                d = datetime(yyyy, mm, dd).date()
                return d.isoformat()
            except ValueError:
                pass
    return None


def _mentions_close(txt: str) -> bool:
    t = _norm(txt)
    if any(k in t for k in NEGATIVE_CANCEL):
        return False
    return any(k in t for k in CLOSE_VERBS)


def detect_booking_intent(message: str) -> Dict:
    """
    Retorna:
    {
      'closed': bool,              # se tem os 3 pilares (lugar+quando+pagamento) OU usuário disse "fechado" etc.
      'place': 'meu_local'|'motel'|'casa_cliente'|None,
      'pay': 'pix'|'dinheiro'|'cartao'|None,
      'date': 'YYYY-MM-DD'|None,
      'time': 'HH:MM'|None,
      'reason': 'explain string'
    }
    """
    msg = message or ""
    place = _find_place(msg)
    pay   = _find_payment(msg)
    tm    = _find_time(msg)
    dt    = _find_date(msg)
    said_close = _mentions_close(msg)

    have_when = bool(tm or dt)
    closed = (place is not None) and (pay is not None) and have_when
    if not closed and said_close and ((place and (tm or dt)) or (pay and (tm or dt)) or (place and pay)):
        # reforça "fechado" mesmo faltando uma peça
        closed = True

    reason = []
    if closed: reason.append("all set (place/pay/when) or user confirmed close")
    if place: reason.append(f"place={place}")
    if pay:   reason.append(f"pay={pay}")
    if dt:    reason.append(f"date={dt}")
    if tm:    reason.append(f"time={tm}")
    if said_close: reason.append("said_close=True")

    return {
        "closed": closed,
        "place": place,
        "pay": pay,
        "date": dt,
        "time": tm,
        "reason": "; ".join(reason) or "no strong signal",
    }