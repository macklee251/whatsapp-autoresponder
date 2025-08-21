# app.py
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ========= ENV =========
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

API_URL          = os.getenv("API_URL", "https://api.ultramsg.com")
ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRAMSG_TOKEN    = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")
MY_WA_NUMBER      = (os.getenv("MY_WA_NUMBER") or "").lstrip("+")  # número da modelo (mesmo da instância)

# Email/AI (mantidos para health)
SMTP_USER  = os.getenv("SMTP_USER")
SMTP_PASS  = os.getenv("SMTP_PASS")
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter")
AI_MODEL    = os.getenv("AI_MODEL") or os.getenv("AI_MODEL_POOL", "")

# ========= IA =========
# usa a função com fallback que você já ajustou no ai_provider.py
try:
    from ai_provider import generate_reply  # assinatura: generate_reply(user_text, profile=None)
except Exception as e:
    print("[AI] aviso: falha import generate_reply:", e)
    def generate_reply(txt: str, profile: Optional[Dict]=None) -> str:
        return "Me diz o bairro e o horário que te atende, amor, e a forma de pagamento (pix/cartão/dinheiro) que confirmo pra você. 💕"

# ========= LOGGING / APP =========
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("app")
app = Flask(__name__)

# ========= HELPERS =========
def normalize_jid(s: Optional[str]) -> str:
    """'5562...@c.us' -> '5562...'; remove '+' e espaços."""
    if not s:
        return ""
    return s.split("@")[0].replace(" ", "").lstrip("+")

def ultramsg_send_text(to_number_e164: str, text: str) -> bool:
    """Envia texto via UltraMsg (form-data + token em params)."""
    if not (ULTRA_INSTANCE_ID and ULTRAMSG_TOKEN):
        log.error("[ULTRA] credenciais ausentes")
        return False
    url = f"{API_URL}/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    data = {"to": to_number_e164, "body": text}
    try:
        log.info("[ULTRA] POST %s?token=*** data=%s", url, {**data, "body": (text[:80] + "…" if len(text) > 80 else text)})
        r = requests.post(url, params=params, data=data, timeout=20)
        try:
            j = r.json()
        except Exception:
            j = {"raw": r.text}
        log.info("[ULTRA] resp %s %s", r.status_code, j)
        # Considera sucesso se não retornar 'error' e status 200
        if r.ok and not j.get("error"):
            return True
        return False
    except Exception as e:
        log.error("[ULTRA] erro envio: %s", e)
        return False

def extract_messages(payload: Any) -> List[Dict[str, Any]]:
    """
    Normaliza o payload do UltraMsg:
    - pode vir como dict com 'data'
    - ou dict direto
    - ou lista de eventos
    Retorna lista de mensagens normalizadas com campos: from, to, body, type, fromMe
    """
    out: List[Dict[str, Any]] = []

    def _coerce_one(ev: Dict[str, Any]):
        msg = ev.get("data") or ev  # alguns eventos vêm envelopados
        body = msg.get("body") or (msg.get("text") or {}).get("body") or ""
        out.append({
            "from": msg.get("from") or msg.get("sender") or msg.get("author") or "",
            "to":   msg.get("to")   or msg.get("chatId") or msg.get("receiver") or "",
            "type": (msg.get("type") or "").lower(),
            "body": body,
            "fromMe": bool(msg.get("fromMe") or msg.get("self") or False),
            "raw": msg,
        })

    if isinstance(payload, list):
        for ev in payload:
            if isinstance(ev, dict):
                _coerce_one(ev)
    elif isinstance(payload, dict):
        _coerce_one(payload)
    else:
        log.warning("[PARSE] payload inesperado: %r", type(payload))

    return out

# ========= ROTAS =========
@app.get("/health")
def health():
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "ai_provider": AI_PROVIDER,
        "ai_model": AI_MODEL,
        "smtp_ready": bool(SMTP_USER and SMTP_PASS),
        "smtp_missing": [k for k,v in {"SMTP_USER":SMTP_USER,"SMTP_PASS":SMTP_PASS}.items() if not v],
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    msgs = extract_messages(payload)

    for m in msgs:
        wa_from = normalize_jid(m["from"])
        wa_to   = normalize_jid(m["to"])
        mtype   = m["type"]
        body    = (m["body"] or "").strip()
        from_me = m["fromMe"]

        # cliente = quem mandou (quando inbound)
        client_number = wa_from
        provider_number = wa_to

        log.info("[INBOUND] prov:%s <- cli:%s | %r (type=%s, fromMe=%s)", provider_number, client_number, body, mtype, from_me)

        # Ignora mensagem enviada pela própria modelo (manual)
        if from_me or (MY_WA_NUMBER and wa_from == MY_WA_NUMBER):
            log.info("[FLOW] ignorando porque é fromMe ou do nosso número (%s).", wa_from)
            continue

        # Apenas texto por enquanto
        if mtype != "chat":
            log.info("[FLOW] tipo não suportado agora: %s", mtype)
            continue

        if not body:
            log.info("[FLOW] corpo vazio; ignorando.")
            continue

        # Gera resposta pela IA (sem delay, para testes)
        try:
            reply = generate_reply(body)
        except Exception as e:
            log.error("[AI] erro: %s", e)
            reply = "Amor, me diz o bairro e o horário que prefere, e a forma de pagamento (pix/cartão/dinheiro), que eu confirmo certinho pra você."

        # Envia
        ok = ultramsg_send_text(client_number, reply[:4096])
        if not ok:
            log.error("[FLOW] falha ao enviar resposta para %s", client_number)

    return jsonify({"status":"ok"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    log.info("Servindo na porta %s", port)
    app.run(host="0.0.0.0", port=port)