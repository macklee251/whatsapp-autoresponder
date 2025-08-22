# ai_intent.py
import os, json, re, logging, requests
from pathlib import Path
from dotenv import load_dotenv

log = logging.getLogger("ai_intent")

# carrega .env ao lado do arquivo
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

BASE_URL = "https://openrouter.ai/api/v1"
OR_KEY   = os.getenv("OPENROUTER_API_KEY", "")
INTENT_MODEL = os.getenv("INTENT_MODEL", "meta-llama/llama-3.1-8b-instruct:free")

HEADERS = {
    "Authorization": f"Bearer {OR_KEY}" if OR_KEY else "",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://localhost",
    "X-Title": "whatsapp-autoresponder",
}

SYSTEM = (
    "Você extrai intenção de agendamento. Responda SOMENTE um JSON válido, "
    "sem texto extra, no formato exato:\n"
    "{\"has_booking\": true|false, "
    "\"local\": \"meu_local|motel|cliente|null\", "
    "\"hora\": \"texto ou null\", "
    "\"pagamento\": \"pix|dinheiro|cartao|null\"}"
)

def _fallback_regex(t: str) -> dict:
    t = t.lower()

    # local
    local = None
    if "motel" in t:
        local = "motel"
    elif "meu local" in t or "villa rosa" in t or "no seu local" in t:
        local = "meu_local"
    elif "meu ap" in t or "meu apê" in t or "meu ape" in t or "no meu ap" in t or "no meu ape" in t or "no meu apê" in t:
        local = "cliente"

    # hora/data (captura algo para “houve menção”)
    hora = None
    m = re.search(r"\b(\d{1,2})h\b|\b(\d{1,2}[:h]\d{2})\b|\b(hoje|amanh[ãa]|agora|mais\s+tarde|à\s+noite|de\s+manhã|de\s+tarde)\b", t)
    if m:
        hora = next((g for g in m.groups() if g), None)

    # pagamento
    pagamento = None
    if "pix" in t: pagamento = "pix"
    elif "dinheiro" in t: pagamento = "dinheiro"
    elif "cartão" in t or "cartao" in t: pagamento = "cartao"

    has = bool(local and hora and pagamento)
    return {"has_booking": has, "local": local, "hora": hora, "pagamento": pagamento}

def detect_booking(user_text: str) -> dict:
    """Extrai intenção/slots via LLM; cai em regex se falhar."""
    if OR_KEY:
        try:
            body = {
                "model": INTENT_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_text.strip()},
                ],
                "temperature": 0.1,
                "max_tokens": 120,
            }
            r = requests.post(f"{BASE_URL}/chat/completions", headers=HEADERS, json=body, timeout=20)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(txt)
            # normaliza campos ausentes
            for k in ("local","hora","pagamento"):
                if k not in data: data[k] = None
            ret = {
                "has_booking": bool(data.get("has_booking")),
                "local": data.get("local") or None,
                "hora": data.get("hora") or None,
                "pagamento": data.get("pagamento") or None,
            }
            return ret
        except Exception as e:
            log.warning("[INTENT] Falha no LLM (%s) – usando regex fallback.", e)
    return _fallback_regex(user_text or "")