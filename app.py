# app.py
# Flask + UltraMsg + OpenRouter (via ai_provider) + detecção de fechamento (ai_intent)
# Silencia conversa por 12h após fechamento; envia email com dados.

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, request, jsonify

from dotenv import load_dotenv, find_dotenv

# === carga .env robusta (funciona mesmo via gunicorn) ===
dotenv_path = find_dotenv(usecwd=True) or (Path(__file__).resolve().parent / ".env")
load_dotenv(dotenv_path, override=True)

# === logs básicos ===
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

# === env necessários ===
API_URL          = os.getenv("API_URL", "https://api.ultramsg.com")
INSTANCE_ID      = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRAMSG_TOKEN   = os.getenv("ULTRAMSG_TOKEN")
SMTP_SERVER      = os.getenv("SMTP_SERVER")
SMTP_PORT        = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER        = os.getenv("SMTP_USER")
SMTP_PASS        = os.getenv("SMTP_PASS")
ALERT_EMAIL      = os.getenv("ALERT_EMAIL")

if not INSTANCE_ID or not ULTRAMSG_TOKEN:
    log.warning("[CONFIG] faltando ULTRA_INSTANCE_ID/ULTRAMSG_TOKEN")

# === import da IA (pool de modelos com fallback) ===
try:
    from ai_provider import generate_reply  # sua função que chama OpenRouter
except Exception as e:
    log.error("[AI] não consegui importar ai_provider.generate_reply: %s", e)
    def generate_reply(history, system_prompt, model_hint=None):
        return "Oi! Recebi sua mensagem."

# === intent detector ===
try:
    from ai_intent import detect_booking_intent
except Exception as e:
    log.error("[INTENT] não consegui importar ai_intent: %s", e)
    def detect_booking_intent(_): return {"closed": False, "place": None, "pay": None, "date": None, "time": None, "reason": "fallback"}

# === “estado” simples em memória (vamos trocar por DB depois) ===
SILENCE_FOR_HOURS = 12
closed_until = {}  # key: chat_id -> datetime
last_model_message = {}  # se a modelo real respondeu manualmente (futuro: via DB/webhook extra)

# === persona padrão da Gabriele (ajuste à vontade) ===
DEFAULT_PERSONA = os.getenv("DEFAULT_PERSONA", (
    "Você é Gabriele, 20 anos, ninfeta magrinha, 1,60m, loira, peitos pequenos. "
    "Atende no seu local (Villa Rosa), em motel ou no apê do cliente. "
    "Valores: R$300 no seu local; R$500 em motel/apê do cliente. Aceita PIX, cartão e dinheiro. "
    "Estilo namoradinha, romântica e provocante; topa tomar uma cerveja, sair pra jantar. "
    "Nunca negocia desconto. Se pedir mídia, diga que tem fotos/vídeos no site. "
    "Se mandarem áudio/foto/vídeo, peça educadamente texto escrito. "
    "Tópicos ilegais/menores: recuse e encerre. "
    "Tom: leve, direto, carinhoso, menos emojis, erros de português esporádicos (naturais). "
    "Responda em 2–8 frases; se pedirem detalhes do atendimento, pode alongar."
))

SYSTEM_RULES = (
    "Objetivo: convencer a fechar (local, data/horário, pagamento). "
    "Quando o cliente já tiver informado local+horário+pagamento, assuma fechado e NÃO continue oferecendo nada. "
    "Não peça 'confirmação' explícita — a modelo confirma depois."
)

# === Flask ===
app = Flask(__name__)

def normalize_from(wa: str) -> str:
    if not wa:
        return ""
    return wa.replace("@c.us", "").replace("@g.us", "")

def send_ultra_text(to_number: str, text: str) -> bool:
    import requests
    url = f"{API_URL}/{INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    payload = {"to": f"{to_number}@c.us", "body": text}
    try:
        log.info("[ULTRA] URL: %s", url)
        r = requests.post(url, params=params, json=payload, timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        log.info("[ULTRA] resp: %s %s", r.status_code, body)
        return r.ok
    except Exception as e:
        log.exception("[ULTRA] falha ao enviar: %s", e)
        return False

def send_email_alert(dest_email: str, subject: str, html_body: str) -> bool:
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and dest_email):
        log.warning("[EMAIL] SMTP não configurado ou destino ausente; skip.")
        return False
    msg = MIMEText(html_body, "html", "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = dest_email
    msg["Subject"] = subject
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [dest_email], msg.as_string())
        log.info("[EMAIL] enviado para %s", dest_email)
        return True
    except Exception as e:
        log.exception("[EMAIL] erro ao enviar: %s", e)
        return False

@app.get("/health")
def health():
    return jsonify({
        "ai_provider": "openrouter",
        "smtp_ready": bool(SMTP_SERVER and SMTP_USER and SMTP_PASS),
        "ultra_instance": INSTANCE_ID,
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        log.info("[INBOUND] %s", json.dumps(payload, ensure_ascii=False))
        data = payload.get("data", {})
        msg_type = data.get("type")
        body = (data.get("body") or "").strip()
        wa_from = data.get("from") or ""
        wa_to   = data.get("to") or ""  # número da “modelo” da instância
        from_number = normalize_from(wa_from)
        to_number   = normalize_from(wa_to)

        # se enviaram mídia/áudio: recuse gentilmente e peça texto
        if msg_type in {"ptt", "audio", "voice", "image", "video", "document"}:
            politely = "Lindinho, não consigo ouvir/abrir áudio ou mídia aqui 😔 Me manda por texto? Prometo te responder rapidinho."
            send_ultra_text(from_number, politely)
            return jsonify({"ok": True})

        # se a conversa já está silenciada (fechado recentemente), não responde
        now = datetime.utcnow()
        until = closed_until.get(from_number)
        if until and now < until:
            log.info("[FLOW] silenciado até %s p/ %s", until.isoformat(), from_number)
            return jsonify({"ok": True})

        # histórico mínimo (poderíamos guardar no futuro)
        history = [
            {"role": "system", "content": f"{DEFAULT_PERSONA}\n{SYSTEM_RULES}"},
            {"role": "user", "content": body},
        ]

        # chama IA para resposta natural
        reply = generate_reply(history, system_prompt=None, model_hint=None)
        reply = (reply or "").strip()

        # envia resposta
        if reply:
            send_ultra_text(from_number, reply)

        # checa se “fechou”
        intent = detect_booking_intent(body)
        log.info("[INTENT] %s", intent)
        if intent.get("closed"):
            # silencia por 12h
            closed_until[from_number] = now + timedelta(hours=SILENCE_FOR_HOURS)
            # e-mail
            when_txt = " ".join(filter(None, [intent.get("date"), intent.get("time")]))
            place_map = {"meu_local":"meu local (Villa Rosa)","motel":"motel","casa_cliente":"casa do cliente"}
            place_txt = place_map.get(intent.get("place") or "", "não identificado")
            pay_map   = {"pix":"PIX","dinheiro":"dinheiro","cartao":"cartão"}
            pay_txt   = pay_map.get(intent.get("pay") or "", "não identificado")

            html = f"""
            <h3>✔ Novo fechamento</h3>
            <p><b>Cliente:</b> +{from_number}</p>
            <p><b>Local:</b> {place_txt}</p>
            <p><b>Quando:</b> {when_txt or 'não identificado'}</p>
            <p><b>Pagamento:</b> {pay_txt}</p>
            <hr>
            <p>Motivo: {intent.get('reason')}</p>
            """
            send_email_alert(ALERT_EMAIL, "Novo cliente quer marcar", html)

        return jsonify({"ok": True})
    except Exception as e:
        log.exception("erro no webhook: %s", e)
        return jsonify({"ok": False}), 200  # sempre 200 pro provedor não re-tentar infinito

@app.get("/")
def root():
    return "ok", 200

if __name__ == "__main__":
    # Para testes locais (no servidor em foreground)
    app.run(host="0.0.0.0", port=8000)