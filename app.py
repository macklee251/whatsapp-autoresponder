import os
import time
import random
import logging
import requests
from pathlib import Path
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ==== CONFIGURAÇÃO BÁSICA ====
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID")
ULTRAMSG_TOKEN   = os.getenv("ULTRAMSG_TOKEN")
MY_WA_NUMBER     = os.getenv("MY_WA_NUMBER")  # número da modelo (sem +)
ALERT_EMAIL      = os.getenv("ALERT_EMAIL", "mlee251@icloud.com")

from ai_provider import generate_reply_with_fallback as generate_reply

# Configuração de logs
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = Flask(__name__)

# ==== ULTRAMSG ====
def ultra_send(to_number: str, message: str):
    url = f"https://api.ultramsg.com/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    payload = {"to": to_number, "body": message}

    log.info("[ULTRA] URL: %s", url)
    resp = requests.post(url, params=params, json=payload)
    log.info("[ULTRA] resp: %s %s", resp.status_code, resp.text)
    return resp.status_code == 200

# ==== FLUXO ====
def process_message(chat_id: str, from_number: str, text: str):
    """
    Processa uma msg recebida:
    - ignora se foi a própria modelo (MY_WA_NUMBER)
    - chama IA para gerar resposta
    - envia pelo UltraMsg
    """
    if from_number == MY_WA_NUMBER:
        log.info("[FLOW] Ignorando msg da própria modelo (%s)", from_number)
        return

    # IA (sem delay humano durante os testes)
    # delay = random.randint(40, 120)
    # log.info("[FLOW] aguardando %ss antes de chamar a IA…", delay)
    # time.sleep(delay)

    log.info("[FLOW] chamando IA…")
    reply = generate_reply(text)

    if not reply:
        log.warning("[AI] não retornou resposta")
        return

    # envia resposta
    ok = ultra_send(from_number, reply)
    if ok:
        log.info("[FLOW] resposta enviada para %s", from_number)
    else:
        log.error("[FLOW] falha ao enviar resposta para %s", from_number)

# ==== WEBHOOK ====
@app.route("/ultra-webhook", methods=["POST"])
def ultra_webhook():
    data = request.json
    log.info("[INBOUND] %s", data)

    if not data:
        return jsonify({"status": "no data"}), 400

    try:
        chat_id = data.get("id", "")
        from_number = data.get("from", "").replace("+", "")
        text = data.get("body", "")

        if text and from_number:
            log.info("[INBOUND] prov:%s <- cli:%s | '%s' (type=%s)",
                     ULTRA_INSTANCE_ID, from_number, text, data.get("type"))
            process_message(chat_id, from_number, text)

    except Exception as e:
        log.exception("Erro processando webhook: %s", e)

    return jsonify({"status": "ok"})

# ==== HEALTH ====
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "ai_provider": os.getenv("AI_PROVIDER"),
        "ai_model": os.getenv("AI_MODEL"),
        "smtp_ready": bool(os.getenv("SMTP_USER")),
        "smtp_missing": [k for k in ["SMTP_USER","SMTP_PASS"] if not os.getenv(k)]
    })

# ==== MAIN ====
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    log.info(f"Servidor iniciado na porta {port}")
    app.run(host="0.0.0.0", port=port)