# ai_intent.py
import re
from datetime import datetime
from typing import Dict, Any, Optional

# horário: "20h", "20:30", "às 19", "hoje", "amanhã", etc.
TIME_RE = re.compile(
    r"\b((?:[01]?\d|2[0-3])[:h\.]?\d{0,2})\b|(?:de\s*manhã|de\s*tarde|à\s*noite|agora|mais tarde|hoje|amanh[ãa])",
    re.IGNORECASE
)

LOC_WORDS = [
    "meu local", "meu ap", "meu apê", "meu apartamento", "meu endereço",
    "villa rosa", "vila rosa", "motel", "no seu ap", "na sua casa",
    "no meu", "na sua"
]

PAY_WORDS = ["pix", "cartão", "cartao", "dinheiro", "cash", "credito", "débito", "debito"]

def _has_location(text: str) -> Optional[str]:
    low = text.lower()
    for w in LOC_WORDS:
        if w in low:
            return w
    return None

def _has_payment(text: str) -> Optional[str]:
    low = text.lower()
    for w in PAY_WORDS:
        if w in low:
            return w
    return None

def _has_time(text: str) -> Optional[str]:
    m = TIME_RE.search(text)
    if m:
        return m.group(0)
    return None

def detect_intent_and_fill_state(conv_state: Dict[str, Any], msg_text: str) -> Dict[str, Any]:
    """
    Atualiza 'conv_state' com o que foi detectado no texto do cliente.
    conv_state exemplo:
      {
        "location": None|"meu local"/"motel"/"apê"...,
        "time": None|"20h"/"21:30"/"à noite"...,
        "payment": None|"pix"/"dinheiro"/"cartao"...,
        "closed": False,
        "silence_until": datetime|str|None
      }
    """
    if not conv_state:
        conv_state = {"location": None, "time": None, "payment": None, "closed": False, "silence_until": None}

    text = (msg_text or "").strip()

    loc = _has_location(text)
    if loc and not conv_state.get("location"):
        # normalização básica de rótulos
        v = loc.lower()
        if "motel" in v:
            conv_state["location"] = "motel"
        elif "villa" in v or "meu local" in v or "seu local" in v:
            conv_state["location"] = "meu_local"
        else:
            conv_state["location"] = "cliente"  # apê/casa do cliente

    tm = _has_time(text)
    if tm and not conv_state.get("time"):
        conv_state["time"] = tm

    pay = _has_payment(text)
    if pay and not conv_state.get("payment"):
        pay = pay.lower().replace("ã", "a")
        conv_state["payment"] = "cartao" if "cart" in pay else pay

    if conv_state.get("location") and conv_state.get("time") and conv_state.get("payment"):
        conv_state["closed"] = True

    return conv_state