import os
import json
import time
import ssl
import smtplib
import requests
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# IA (arquivo separado ai_provider.py — já deve existir no mesmo diretório)
from ai_provider import ai_reply

load_dotenv()

app = Flask(__name__)

# ====== Configurações do UltraMsg / API ======
API_URL     = os.getenv("API_URL", "https://api.ultramsg.com")
INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRA_TOKEN = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

# ====== Config SMTP (Gmail com senha de app) ======
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")          # ex: atendimento.jobbot@gmail.com
SMTP_PASS   = os.getenv("SMTP_PASS")          # senha de app (não a senha normal)
SMTP_FROM   = os.getenv("SMTP_FROM") or SMTP_USER
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "mlee251@icloud.com")

# ====== Persona padrão (depois buscaremos do banco por número) ======
DEFAULT_PERSONA = os.getenv(
    "DEFAULT_PERSONA",
    "Atendente adulta, educada e persuasiva; nunca negocia desconto; foca em marcar local, data e forma de pagamento; sem menores e sem ilegalidades; se receber mídia/áudio, pede texto com delicadeza."
)

# ====== Silêncio (12h) por par (provedor, cliente) ======
# chave: (provider_number, client_e164) -> datetime UTC até quando silenciar
SILENCE = {}

# ----------------- Helpers -----------------
def now_utc():
    return datetime.now(timezone.utc)

def normalize_jid(j: str) -> str:
    # "5511999999999@c.us" -> "5511999999999"
    return (j or "").split("@")[0].lstrip("+").strip()

def is_silenced(provider_number: str, client_e164: str) -> bool:
    until = SILENCE.get((provider_number, client_e164))
    return bool(until and until > now_utc())

def set_silence(provider_number: str, client_e164: str, hours: int = 12):
    SILENCE[(provider_number, client_e164)] = now_utc() + timedelta(hours=hours)
    print(f"[SILENCE] {provider_number} ~ {client_e164} até {SILENCE[(provider_number, client_e164)]}")

def smtp_ready():
    missing = []
    for k in ("SMTP_SERVER", "SMTP_USER", "SMTP_PASS"):
        if not os.getenv(k):
            missing.append(k)
    return (len(missing) == 0, missing)

def send_email_quero_marcar(to_email: str, client_phone: str, original_text: str) -> bool:
    ok, missing = smtp_ready()
    if not ok or not to_email:
        print(f"[EMAIL] SMTP não configurado ou destino ausente; faltando: {missing}. Skip.")
        return False

    subject = "Novo cliente querendo marcar"
    body = (
        f"Cliente: {client_phone}\n"
        f"Mensagem: {original_text}\n"
        f"Data/Hora: {datetime.now().isoformat(timespec='seconds')}\n"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print("[EMAIL] enviado.")
        return True
    except Exception as e:
        print("[EMAIL] erro:", e)
        return False

def send_text(to_number_e164: str, text: str) -> bool:
    """Envia mensagem de texto via UltraMsg."""
    if not (INSTANCE_ID and ULTRA_TOKEN):
        print("[ULTRA] credenciais ausentes.")
        return False
    url = f"{API_URL}/{INSTANCE_ID}/messages/chat"
    payload = {"to": to_number_e164, "body": text}
    try:
        r = requests.post(url, data=payload, timeout=15, params={"token": ULTRA_TOKEN})
        if not r.ok:
            print("[ULTRA] erro:", r.status_code, r.text)
        return r.ok
    except Exception as e:
        print("[ULTRA] exceção:", e)
        return False

# ----------------- Rotas -----------------
@app.get("/health")
def health():
    ok, missing = smtp_ready()
    return jsonify({
        "smtp_ready": ok,
        "smtp_missing": missing,
        "ultra_instance": INSTANCE_ID,
        "ai_provider": os.getenv("AI_PROVIDER", "openrouter"),
        "ai_model": os.getenv("AI_MODEL", ""),
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    """
    Lida com eventos da UltraMsg:
    - Inbound (cliente -> modelo): responder com IA (ou fallback), pedir texto se vier mídia.
    - fromMe=True (modelo respondeu manualmente): silenciar 12h essa conversa.
    - Se o cliente disser "quero marcar": enviar e-mail, silenciar 12h e confirmar ao cliente.
    """
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    events = payload if isinstance(payload, list) else [payload]

    for ev in events:
        msg = ev.get("data") or ev.get("message") or ev

        # Campos comuns na UltraMsg (variam um pouco conforme o webhook selecionado)
        from_me = bool(msg.get("fromMe") or msg.get("self"))
        wa_from  = msg.get("from") or msg.get("chatId")
        wa_to    = msg.get("to") or msg.get("receiver")
        mtype    = (msg.get("type") or "").lower()
        body     = (msg.get("body") or (msg.get("text") or {}).get("body") or "").strip()

        origin = normalize_jid(wa_from)
        target = normalize_jid(wa_to)

        # Se a modelo (dona do número) respondeu manualmente => silêncio 12h
        if from_me:
            provider_number = origin
            client_number   = target
            print(f"[FROM-ME] prov:{provider_number} -> cli:{client_number} | '{body}'")
            set_silence(provider_number, client_number, 12)
            continue

        # Caso contrário: inbound (cliente -> modelo)
        client_number   = origin
        provider_number = target
        print(f"[INBOUND] prov:{provider_number} <- cli:{client_number} | '{body}' (type={mtype})")

        # Respeita silêncio ativo
        if is_silenced(provider_number, client_number):
            print("[MUTE] conversa silenciada; ignorando.")
            continue

        # Detecta fechamento simples
        if "quero marcar" in body.lower():
            send_email_quero_marcar(ALERT_EMAIL, f"+{client_number}", body)
            set_silence(provider_number, client_number, 12)
            # Confirmação curta ao cliente (opcional)
            send_text(client_number, "Perfeito! Vou confirmar os detalhes e já te retorno 💌")
            continue

        # Se for mídia/áudio, peça texto de forma educada
        if mtype in {"audio", "voice", "ptt", "video", "image", "document", "sticker"}:
            reply = "Amor, vi sua mensagem 💬. Pra te atender direitinho, me manda em texto, tá? 😘"
            send_text(client_number, reply)
            continue

        # Mensagem de texto -> IA com persona padrão (depois ligaremos ao banco por número)
        try:
            reply = ai_reply(body or "Cliente iniciou conversa.", persona=DEFAULT_PERSONA)
            if not reply or len(reply) < 2:
                reply = "Perfeito, posso te atender sim 💖. Me diz o bairro e o melhor horário?"
        except Exception as e:
            print("[AI] erro:", e)
            reply = "Certo! Mensagem recebida 😉. Me diz o bairro e o melhor horário?"

        send_text(client_number, reply)

    return jsonify({"status": "ok"}), 200

# ----------------- Main -----------------
if __name__ == "__main__":
    # Em produção, rodar com gunicorn: venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
    app.run(host="0.0.0.0", port=8000)