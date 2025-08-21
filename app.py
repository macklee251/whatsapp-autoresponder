import os
import ssl
import smtplib
import requests
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify
from dotenv import load_dotenv, find_dotenv

# ===== Carregamento ROBUSTO do .env =====
ENV_PATH = find_dotenv(usecwd=True)
if not ENV_PATH:
    # força caminho do arquivo ao lado do app.py
    ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=str(ENV_PATH), override=True)
print("[ENV] carregado de:", ENV_PATH)

# ===== IA =====
from ai_provider import ai_reply  # usa AI_PROVIDER/AI_MODEL do .env

app = Flask(__name__)

# ===== Config UltraMsg =====
API_URL     = os.getenv("API_URL", "https://api.ultramsg.com")
INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRA_TOKEN = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

# ===== Config SMTP =====
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")  # senha de app (sem espaços)
SMTP_FROM   = os.getenv("SMTP_FROM") or SMTP_USER
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "mlee251@icloud.com")

# ===== Persona padrão =====
DEFAULT_PERSONA = os.getenv(
    "DEFAULT_PERSONA",
    "Atendente adulta, educada e persuasiva; nunca negocia desconto; foca em marcar local, data e forma de pagamento; sem menores e sem ilegalidades; se receber mídia/áudio, pede texto com delicadeza."
)

# ===== Silêncio (12h) por par (provedor, cliente) =====
SILENCE = {}  # dict[(provider_number, client_e164)] = datetime UTC

# ===== Helpers =====
def now_utc():
    return datetime.now(timezone.utc)

def normalize_jid(j: str) -> str:
    return (j or "").split("@")[0].lstrip("+").strip()

def is_silenced(provider_number: str, client_e164: str) -> bool:
    until = SILENCE.get((provider_number, client_e164))
    return bool(until and until > now_utc())

def set_silence(provider_number: str, client_e164: str, hours: int = 12):
    SILENCE[(provider_number, client_e164)] = now_utc() + timedelta(hours=hours)
    print(f"[SILENCE] {provider_number} ~ {client_e164} até {SILENCE[(provider_number, client_e164)]}")

def smtp_ready():
    missing = [k for k in ("SMTP_SERVER", "SMTP_USER", "SMTP_PASS") if not os.getenv(k)]
    return (len(missing) == 0, missing)

def send_email_quero_marcar(to_email: str, client_phone: str, original_text: str) -> bool:
    ok, missing = smtp_ready()
    if not ok or not to_email:
        print(f"[EMAIL] SMTP não configurado ou destino ausente; faltando: {missing}. Skip.")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = "Novo cliente querendo marcar"
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.set_content(
            f"Cliente: {client_phone}\n"
            f"Mensagem: {original_text}\n"
            f"Data/Hora: {datetime.now().isoformat(timespec='seconds')}\n"
        )
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
        print("[ULTRA] credenciais ausentes (ULTRA_INSTANCE_ID/ULTRAMSG_TOKEN).")
        return False
    url = f"{API_URL}/{INSTANCE_ID}/messages/chat"
    payload = {"to": to_number_e164, "body": text}
    try:
        r = requests.post(url, data=payload, params={"token": ULTRA_TOKEN}, timeout=15)
        if not r.ok:
            print("[ULTRA] erro:", r.status_code, r.text)
        return r.ok
    except Exception as e:
        print("[ULTRA] exceção:", e)
        return False

# ===== Rotas =====
@app.get("/health")
def health():
    smtp_ok, missing = smtp_ready()
    return jsonify({
        "ai_provider": os.getenv("AI_PROVIDER", ""),
        "ai_model": os.getenv("AI_MODEL", ""),
        "smtp_ready": smtp_ok,
        "smtp_missing": missing,
        "ultra_instance": INSTANCE_ID,
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    """
    - fromMe=True: modelo respondeu manualmente -> silenciar 12h.
    - Cliente diz "quero marcar": envia e-mail, silencia 12h e responde confirmação.
    - Mídias/áudio: pede texto.
    - Texto: chama IA (fallback educado se falhar).
    """
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    events = payload if isinstance(payload, list) else [payload]

    for ev in events:
        msg = ev.get("data") or ev.get("message") or ev

        from_me = bool(msg.get("fromMe") or msg.get("self"))
        wa_from  = msg.get("from") or msg.get("chatId")
        wa_to    = msg.get("to") or msg.get("receiver")
        mtype    = (msg.get("type") or "").lower()
        body     = (msg.get("body") or (msg.get("text") or {}).get("body") or "").strip()

        origin = normalize_jid(wa_from)
        target = normalize_jid(wa_to)

        if from_me:
            provider_number = origin
            client_number   = target
            print(f"[FROM-ME] prov:{provider_number} -> cli:{client_number} | '{body}'")
            set_silence(provider_number, client_number, 12)
            continue

        client_number   = origin
        provider_number = target
        print(f"[INBOUND] prov:{provider_number} <- cli:{client_number} | '{body}' (type={mtype})")

        if is_silenced(provider_number, client_number):
            print("[MUTE] conversa silenciada; ignorando.")
            continue

        if "quero marcar" in body.lower():
            send_email_quero_marcar(ALERT_EMAIL, f"+{client_number}", body)
            set_silence(provider_number, client_number, 12)
            send_text(client_number, "Perfeito! Vou confirmar os detalhes e já te retorno 💌")
            continue

        if mtype in {"audio", "voice", "ptt", "video", "image", "document", "sticker"}:
            reply = "Amor, vi sua mensagem 💬. Pra te atender direitinho, me manda em texto, tá? 😘"
            print(f"[ULTRA] enviando para +{client_number}: {reply!r}")
            send_text(client_number, reply)
            continue

        print("[FLOW] preparando resposta IA…")
        try:
            reply = ai_reply(body or "Cliente iniciou conversa.", persona=DEFAULT_PERSONA)
            print("[AI] reply:", (reply[:200] + "...") if reply and len(reply) > 200 else reply)
            if not reply or len(reply.strip()) < 2:
                reply = "Perfeito, posso te atender sim 💖. Me diz o bairro e o melhor horário?"
        except Exception as e:
            print("[AI] erro:", e)
            reply = "Certo! Mensagem recebida 😉. Me diz o bairro e o melhor horário?"

        print(f"[ULTRA] enviando para +{client_number}: {reply[:160]!r}")
        ok = send_text(client_number, reply)
        print(f"[ULTRA] status de envio: {'OK' if ok else 'ERRO'}")

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # Desenvolvimento: python app.py
    # Produção: venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
    app.run(host="0.0.0.0", port=8000)