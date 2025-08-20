# app.py
import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ---------- carga do .env ----------
load_dotenv()  # <— IMPORTANTE: carrega as variáveis do arquivo .env

# ---------- UltraMsg ----------
ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID", "").strip()
ULTRAMSG_TOKEN    = (os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN") or "").strip()
API_URL           = (os.getenv("API_URL") or "https://api.ultramsg.com").rstrip("/")
ULTRA_BASE        = f"{API_URL}/{ULTRA_INSTANCE_ID}"

# Diagnóstico UltraMsg
if not ULTRA_INSTANCE_ID or not ULTRAMSG_TOKEN:
    raise SystemExit("[CONFIG] Faltam ULTRA_INSTANCE_ID e/ou ULTRAMSG_TOKEN no .env")

# ---------- SMTP / Email ----------
SMTP_SERVER = os.getenv("SMTP_SERVER", "").strip()
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER   = os.getenv("SMTP_USER", "").strip()
SMTP_PASS   = os.getenv("SMTP_PASS", "").strip()
EMAIL_FROM  = (os.getenv("EMAIL_FROM") or SMTP_USER).strip()

TO_EMAIL_ON_BOOK = "mlee251@icloud.com"  # fixo para este teste

# ---------- utilidades ----------
def normalize_from(wa_from: str) -> str:
    """'5562...@c.us' -> '5562...'"""
    return wa_from.split("@")[0].lstrip("+")

def send_ok(to_number_e164: str) -> bool:
    url = f"{ULTRA_BASE}/messages/chat"
    data = {"token": ULTRAMSG_TOKEN, "to": to_number_e164, "body": "ok"}
    try:
        r = requests.post(url, data=data, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print("[SEND ERRO]", e)
        try:
            print("resp:", r.text)
        except Exception:
            pass
        return False

# ---------- e-mail ----------
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

def email_config_ok() -> tuple[bool, list[str]]:
    missing = []
    if not SMTP_SERVER: missing.append("SMTP_SERVER")
    if not SMTP_PORT:   missing.append("SMTP_PORT")
    if not SMTP_USER:   missing.append("SMTP_USER")
    if not SMTP_PASS:   missing.append("SMTP_PASS")
    return (len(missing) == 0, missing)

def send_email_quero_marcar(to_email: str, client_phone: str, raw_text: str) -> bool:
    ok, missing = email_config_ok()
    if not ok or not to_email:
        # diagnóstico explícito
        miss = ", ".join(missing + ([] if to_email else ["destinatario"]))
        print(f"[EMAIL] SMTP não configurado ou destino ausente; faltando: {miss}. Skip.")
        return False

    subject = "Novo contato: cliente quer marcar"
    body = (
        "Olá,\n\n"
        f"O cliente {client_phone} enviou a mensagem: \"{raw_text}\"\n\n"
        "Assunto: \"quero marcar\".\n\n"
        "— Sistema de Notificações\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Atendimento JobBot", EMAIL_FROM or SMTP_USER))
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)  # SMTP_PASS = senha de app do Gmail
            server.sendmail(msg["From"], [to_email], msg.as_string())
        print(f"[EMAIL] Enviado para {to_email}")
        return True
    except Exception as e:
        print("[EMAIL ERRO]", e)
        return False

# ---------- Flask ----------
app = Flask(__name__)
SEEN = set()  # idempotência simples

@app.route("/health", methods=["GET"])
def health():
    # mostra rapidamente se o SMTP está ok
    ok, missing = email_config_ok()
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "smtp_ready": ok,
        "smtp_missing": missing,
    }), 200

@app.route("/test-email", methods=["GET"])
def test_email():
    ok = send_email_quero_marcar(TO_EMAIL_ON_BOOK, "+5562999999999", "quero marcar")
    return ("ok" if ok else "erro"), 200

@app.route("/ultra-webhook", methods=["POST"])
def ultra_webhook():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    events = payload if isinstance(payload, list) else [payload]

    for ev in events:
        msg = ev.get("data") or ev.get("message") or ev

        # ignora mensagens enviadas por nós
        if msg.get("fromMe") or msg.get("self"):
            continue

        # id da mensagem (idempotência)
        msg_id = msg.get("id") or msg.get("messageId") or (msg.get("key") or {}).get("id")
        if msg_id and msg_id in SEEN:
            continue
        if msg_id:
            SEEN.add(msg_id)

        wa_from = msg.get("from") or msg.get("chatId") or (msg.get("sender") or {}).get("id") or ""
        if not wa_from:
            continue
        user_number = normalize_from(wa_from)

        text = msg.get("body")
        if text is None and isinstance(msg.get("text"), dict):
            text = msg["text"].get("body")
        text = (text or "").strip()

        print(f"-> {user_number}: {text!r}")

        # dispara e-mail se contiver "quero marcar"
        if "quero marcar" in text.lower():
            send_email_quero_marcar(TO_EMAIL_ON_BOOK, f"+{user_number}", text)

        # responde sempre "ok"
        send_ok(user_number)

    return jsonify({"status": "ok"}), 200

# alias
@app.route("/webhook", methods=["POST"])
def webhook_alias():
    return ultra_webhook()

if __name__ == "__main__":
    print("UltraMsg Instance:", ULTRA_INSTANCE_ID)
    print("SMTP_USER:", SMTP_USER or "(vazio)")
    app.run(host="0.0.0.0", port=8000)