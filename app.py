# app.py
import os, re, smtplib, logging, random
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests

from ai_intent import detect_booking
from ai_provider import generate_reply

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = Flask(__name__)

# UltraMsg
ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRAMSG_TOKEN    = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")
API_URL           = os.getenv("API_URL", "https://api.ultramsg.com")

# E-mail
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

# IA / Persona
DEFAULT_PERSONA = os.getenv("DEFAULT_PERSONA", "Atendente educada e persuasiva.")

# Silêncio pós-fechamento (em horas)
SILENCE_HOURS = float(os.getenv("SILENCE_HOURS", "0"))

# Sessão em memória
SESS = {}  # {client_number: {"history":[...], "silence_until": datetime|None}}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def now_utc():
    return datetime.now(timezone.utc)

def normalize_wa_number(s: str) -> str:
    """Converte '5562xxxx@c.us' -> '+5562xxxx'."""
    if not s: return s
    digits = re.sub(r"\D", "", s)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits

def is_silenced(client_number: str) -> bool:
    info = SESS.get(client_number)
    if not info: return False
    su = info.get("silence_until")
    return bool(su and now_utc() < su)

def set_silence(client_number: str, hours: float):
    if hours <= 0:  # testes: desliga silêncio
        SESS.setdefault(client_number, {}).pop("silence_until", None)
        return
    until = now_utc() + timedelta(hours=hours)
    SESS.setdefault(client_number, {})["silence_until"] = until

def ultra_send_text(to_number: str, body: str) -> bool:
    """Envia texto via UltraMsg: token vai na query-string (requisito da API)."""
    to = re.sub(r"\D", "", to_number)  # só dígitos
    url = f"{API_URL}/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    data = {"to": to, "body": body}
    try:
        log.info("[ULTRA] POST %s?token=*** data=%s", url, {**data, "body": body[:80]+"..." if len(body)>80 else body})
        r = requests.post(url, params=params, data=data, timeout=20)
        log.info("[ULTRA] status=%s resp=%s", r.status_code, r.text[:300])
        r.raise_for_status()
        # API retorna 200 também para erro lógico; verificar chave 'error'
        j = {}
        try: j = r.json()
        except: pass
        if isinstance(j, dict) and j.get("error"):
            log.error("[ULTRA] erro lógico: %s", j)
            return False
        return True
    except Exception as e:
        log.exception("[ULTRA] falha no envio: %s", e)
        return False

def send_email_alert(subject: str, body: str) -> bool:
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        log.warning("[EMAIL] SMTP não configurado; skip.")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = ALERT_EMAIL

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        log.info("[EMAIL] enviado para %s", ALERT_EMAIL)
        return True
    except Exception as e:
        log.exception("[EMAIL] falha ao enviar alerta: %s", e)
        return False

def push_history(client: str, role: str, content: str):
    entry = {"role": role, "content": content}
    SESS.setdefault(client, {}).setdefault("history", []).append(entry)
    # mantém últimas 8 trocas
    if len(SESS[client]["history"]) > 16:
        SESS[client]["history"] = SESS[client]["history"][-16:]

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "smtp_ready": bool(SMTP_SERVER and SMTP_USER and SMTP_PASS),
        "ai_provider": os.getenv("AI_PROVIDER"),
        "intent_model": os.getenv("INTENT_MODEL"),
        "reply_model": os.getenv("REPLY_MODEL") or os.getenv("AI_MODEL"),
        "silence_hours": SILENCE_HOURS,
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    payload = request.get_json(silent=True) or {}
    log.info("[INBOUND] %s", payload)

    data = (payload or {}).get("data") or {}
    txt  = (data.get("body") or "").strip()
    typ  = data.get("type") or ""
    frm  = data.get("from") or ""
    to   = data.get("to") or ""
    from_me = bool(data.get("fromMe"))

    client_number = normalize_wa_number(frm)
    my_number     = normalize_wa_number(to)

    # se a modelo (fromMe=True) respondeu manualmente: ativa silêncio
    if from_me:
        set_silence(client_number, max(SILENCE_HOURS, 12))  # silencia por 12h nesses casos
        return jsonify({"status":"ok"})

    # apenas mensagens de texto
    if typ != "chat" or not txt:
        ultra_send_text(client_number, "Amor, me manda por escrito? Não consigo ver foto/áudio aqui. 💗")
        return jsonify({"status":"ok"})

    # silêncio ativo?
    if is_silenced(client_number):
        log.info("[FLOW] silêncio ativo para %s", client_number)
        return jsonify({"status":"ok"})

    # guarda histórico
    push_history(client_number, "user", txt)

    # 1) Detector de fechamento (slots)
    intent = detect_booking(txt)
    log.info("[INTENT] %s", intent)

    if intent.get("has_booking"):
        loc = intent.get("local")
        hr  = intent.get("hora")
        pg  = intent.get("pagamento")

        if   loc == "meu_local": local_txt, preco = "meu local (Villa Rosa)", 300
        elif loc == "motel":     local_txt, preco = "motel", 500
        else:                    local_txt, preco = "seu apê", 500

        # e-mail para a modelo
        subject = "🛎️ Novo agendamento (bot)"
        body = (f"Cliente: {client_number}\n"
                f"Local: {local_txt}\n"
                f"Horário: {hr}\n"
                f"Pagamento: {pg}\n"
                f"Valor: R$ {preco}\n"
                f"Destino (meu número): {my_number}\n")
        send_email_alert(subject, body)

        ultra_send_text(client_number, "Fechadinho então. Te vejo no combinado. 💋")
        set_silence(client_number, SILENCE_HOURS)
        # registra no histórico
        push_history(client_number, "assistant", "Fechadinho então. Te vejo no combinado.")
        return jsonify({"status":"ok", "closed": True})

    # 2) Resposta natural (persona Gabriele)
    persona = os.getenv("DEFAULT_PERSONA", "Atendente educada e persuasiva.")
    reply = generate_reply(txt, persona, history=SESS.get(client_number, {}).get("history"))
    # limita tamanho do WhatsApp
    reply = reply[:4096]
    ultra_send_text(client_number, reply)
    push_history(client_number, "assistant", reply)

    return jsonify({"status":"ok", "closed": False})

# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # execução direta (para testes locais)
    app.run(host="0.0.0.0", port=8000)