# app.py
import os
import ssl
import smtplib
import time
import random
import requests
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify
from dotenv import load_dotenv, find_dotenv

# ========= .env (carregamento robusto) =========
ENV_PATH = find_dotenv(usecwd=True)
if not ENV_PATH:
    ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=str(ENV_PATH), override=True)
print("[ENV] carregado de:", ENV_PATH)

# ========= IA =========
from ai_provider import generate_reply  # já expõe fallback/pool no seu ai_provider

# ========= Flask =========
app = Flask(__name__)

# ========= Config UltraMsg =========
API_URL     = os.getenv("API_URL", "https://api.ultramsg.com")
INSTANCE_ID = (os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID") or "").strip()
ULTRA_TOKEN = (os.getenv("ULTRAMSG_TOKEN")   or os.getenv("ULTRA_TOKEN")   or "").strip()

# ========= Config SMTP =========
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")   # senha de app (sem espaços)
SMTP_FROM   = os.getenv("SMTP_FROM") or SMTP_USER
ALERT_EMAIL = os.getenv("ALERT_EMAIL")  # para “quero marcar”

# ========= Persona padrão =========
DEFAULT_PERSONA = os.getenv(
    "DEFAULT_PERSONA",
    "Atendente adulta, educada e persuasiva; nunca negocia desconto; "
    "foca em marcar local, data/horário e forma de pagamento; sem menores e sem ilegalidades; "
    "se receber mídia/áudio, pede texto com delicadeza."
)

# ========= Delay humano / workers =========
DELAY_MIN = int(os.getenv("DELAY_MIN_SECONDS", "40"))    # 40s
DELAY_MAX = int(os.getenv("DELAY_MAX_SECONDS", "150"))   # 2m30s
EXECUTOR  = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "4")))

# ========= Silêncio (12h) por par (provedor, cliente) =========
SILENCE = {}  # dict[(provider_number, client_e164)] = datetime UTC

def now_utc():
    return datetime.now(timezone.utc)

def normalize_jid(j: str) -> str:
    """Converte '5562xxxxx@c.us' -> '5562xxxxx' e remove '+'."""
    return (j or "").split("@")[0].lstrip("+").strip()

def is_silenced(provider_number: str, client_e164: str) -> bool:
    until = SILENCE.get((provider_number, client_e164))
    return bool(until and until > now_utc())

def set_silence(provider_number: str, client_e164: str, hours: int = 12):
    SILENCE[(provider_number, client_e164)] = now_utc() + timedelta(hours=hours)
    print(f"[SILENCE] {provider_number} ~ {client_e164} até {SILENCE[(provider_number, client_e164)]}")

# ========= E-mail =========
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

# ========= UltraMsg =========
def send_text(to_number_e164: str, text: str) -> bool:
    """Envia mensagem de texto via UltraMsg e loga a resposta JSON."""
    if not (INSTANCE_ID and ULTRA_TOKEN):
        print("[ULTRA] credenciais ausentes (ULTRA_INSTANCE_ID/ULTRAMSG_TOKEN).")
        return False

    # UltraMsg prefere o token como GET param na URL
    url = f"{API_URL}/{INSTANCE_ID}/messages/chat?token={ULTRA_TOKEN}"
    payload = {"to": to_number_e164, "body": text}

    try:
        r = requests.post(url, data=payload, timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}

        print("[ULTRA] URL:", f"{API_URL}/{INSTANCE_ID}/messages/chat?token=***")
        print("[ULTRA] resp:", r.status_code, body)

        # Sucessos típicos
        if r.ok and (body.get("sent") is True or body.get("status") in ("ok",) or body.get("message") == "Message has been sent"):
            if body.get("id"):
                print("[ULTRA] message_id:", body["id"])
            return True
        return False
    except Exception as e:
        print("[ULTRA] exceção:", e)
        return False

# ========= IA em background =========
def process_with_ai(provider_number: str, client_number: str, user_text: str):
    """Roda após responder 'Ok': espera atraso, gera resposta da IA e envia."""
    try:
        delay = random.randint(DELAY_MIN, DELAY_MAX)
        print(f"[FLOW] aguardando {delay}s antes de chamar a IA…")
        time.sleep(delay)

        reply = generate_reply(user_text, persona=DEFAULT_PERSONA)
        if not reply or len(reply.strip()) < 2:
            reply = "Perfeito 💖 Me diz o bairro e a faixa de horário (manhã/tarde/noite) pra eu confirmar pra você."

        print(f"[ULTRA] enviando IA para +{client_number}: {reply[:160]!r}")
        ok = send_text(client_number, reply[:4096])
        print(f"[ULTRA] status de envio (IA): {'OK' if ok else 'ERRO'}")
    except Exception as e:
        print(f"[AI][ERRO BACKGROUND] {e}")

# ========= Rotas =========
@app.get("/health")
def health():
    smtp_ok, missing = smtp_ready()
    return jsonify({
        "ai_provider": os.getenv("AI_PROVIDER", ""),
        "ai_model": os.getenv("AI_MODEL", "") or os.getenv("AI_MODEL_POOL", ""),
        "smtp_ready": smtp_ok,
        "smtp_missing": missing,
        "ultra_instance": INSTANCE_ID,
        "delay_window_seconds": [DELAY_MIN, DELAY_MAX],
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    """
    Comportamento:
      - fromMe=True  -> modelo respondeu manualmente: silencia 12h.
      - 'quero marcar' no texto -> envia e-mail, silencia 12h, manda confirmação curta e não chama IA.
      - mídia/áudio -> pede gentilmente texto.
      - chat/texto -> responde 'Ok' imediatamente e agenda IA com atraso humano.
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

        origin = normalize_jid(wa_from)  # cliente quando inbound
        target = normalize_jid(wa_to)    # provedor quando inbound

        # Se foi a modelo quem respondeu no WhatsApp (manual), silencia 12h
        if from_me:
            provider_number = origin
            client_number   = target
            print(f"[FROM-ME] prov:{provider_number} -> cli:{client_number} | '{body}'")
            set_silence(provider_number, client_number, 12)
            continue

        client_number   = origin
        provider_number = target
        print(f"[INBOUND] prov:{provider_number} <- cli:{client_number} | '{body}' (type={mtype})")

        # Se conversa está silenciada, ignore
        if is_silenced(provider_number, client_number):
            print("[MUTE] conversa silenciada; ignorando.")
            continue

        # Detecta fechamento: 'quero marcar'
        if "quero marcar" in body.lower():
            send_email_quero_marcar(ALERT_EMAIL, f"+{client_number}", body)
            set_silence(provider_number, client_number, 12)
            send_text(client_number, "Perfeito! Vou confirmar os detalhes e já te retorno 💌")
            continue

        # Se for mídia/áudio/etc, pede texto com educação e encerra
        if mtype in {"audio", "voice", "ptt", "video", "image", "document", "sticker"}:
            reply = "Amor, vi sua mensagem 💬. Pra te atender direitinho, me manda em texto, tá? 😘"
            print(f"[ULTRA] solicitando texto para +{client_number}")
            send_text(client_number, reply)
            continue

        # Só processamos chat/texto
        if mtype != "chat":
            print("[SKIP] tipo de mensagem não suportado agora:", mtype)
            continue

        # 1) Responde 'Ok' imediatamente (feedback instantâneo)
        send_text(client_number, "Ok")

        # 2) Agenda resposta IA com atraso humano (não bloqueia o webhook)
        EXECUTOR.submit(process_with_ai, provider_number, client_number, body)

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # Desenvolvimento (debug no terminal):
    #   source venv/bin/activate && python app.py
    app.run(host="0.0.0.0", port=8000)