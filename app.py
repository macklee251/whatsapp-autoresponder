# app.py
# Flask + UltraMsg + OpenRouter (ai_provider) + detecção de fechamento (ai_intent)
# Silencia conversa por 12h após fechamento; envia e-mail com dados; sem delay artificial.

import os
import re
import json
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv, find_dotenv

# ====== carregar .env (robusto para python direto ou gunicorn) ======
dotenv_path = find_dotenv(usecwd=True)
if not dotenv_path:
    dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path, override=True)

# ====== logs ======
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("app")

# ====== env obrigatórios ======
API_URL        = os.getenv("API_URL", "https://api.ultramsg.com").rstrip("/")
INSTANCE_ID    = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRA_TOKEN    = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

SMTP_SERVER    = os.getenv("SMTP_SERVER")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER")
SMTP_PASS      = os.getenv("SMTP_PASS")
ALERT_EMAIL    = os.getenv("ALERT_EMAIL")

if not INSTANCE_ID or not ULTRA_TOKEN:
    log.warning("[CONFIG] faltando ULTRA_INSTANCE_ID/ULTRAMSG_TOKEN")

# ====== IA (pool OpenRouter) ======
from ai_provider import generate_reply  # assinatura: generate_reply(history, system_prompt=None, model_hint=None)

# ====== detector de fechamento ======
from ai_intent import detect_booking_intent  # retorna dict com closed/place/pay/date/time

# ====== persona e regras ======
DEFAULT_PERSONA = os.getenv("DEFAULT_PERSONA", (
    "Você é Gabriele, 20 anos, ninfeta magrinha, 1,60m, loira, peitos pequenos. "
    "Atende no seu local (Villa Rosa), em motel ou no apê do cliente (se for apartamento). "
    "Valores: R$300 no seu local; R$500 em motel/apê do cliente. Aceita PIX, cartão e dinheiro. "
    "Estilo: namoradinha romântica, carinhosa e provocante; pode beber 1 cerveja, topar jantar. "
    "Nunca negocia desconto. Se pedirem mídia, diga que tem no site; se mandarem áudio/foto/vídeo, peça educadamente texto. "
    "Sem menores e sem atividades ilegais. Pode falar de sexo de forma adulta e direta (sem coisa ilegal). "
    "Responda natural, 2–8 frases (pode alongar se pedirem detalhes). Poucos emojis ou nenhum; erros leves de PT-BR são ok."
))
SYSTEM_RULES = (
    "Objetivo: conduzir para fechar (local + horário/data + pagamento). "
    "Não invente valor/horário/local; se faltou algo, pergunte direto. "
    "Quando o cliente já informou os 3 pontos, considere fechado e NÃO continue oferecendo nada; "
    "apenas finalize carinhosamente. A confirmação final é por conta da modelo real."
)

SILENCE_FOR_HOURS = 12  # silêncio após fechamento

# ====== Flask ======
app = Flask(__name__)

# ====== estado simples em memória (depois iremos para DB) ======
# chaveamos pelo número do cliente (cada conversa)
silence_until = {}  # { client_number: datetime.utcnow() + 12h }

# ====== util ======
def normalize_wa(s: str) -> str:
    """ '5562...@c.us' -> '5562...' (somente dígitos) """
    if not s:
        return ""
    return re.sub(r"\D", "", s.split("@")[0])

def send_ultra_text(to_e164_digits: str, text: str) -> bool:
    """Envia texto via UltraMsg. Token vai na query-string (requisito da API)."""
    url = f"{API_URL}/{INSTANCE_ID}/messages/chat"
    params = {"token": ULTRA_TOKEN}
    # UltraMsg aceita "to" como número cru ou formato JID; usaremos e164 com '+' para segurança
    to = f"+{to_e164_digits}" if not to_e164_digits.startswith("+") else to_e164_digits
    data = {"to": to, "body": text}
    log.info("[ULTRA] POST %s?token=*** data=%s", url, {**data, "body": (text[:80] + "..." if len(text) > 80 else text)})
    try:
        r = requests.post(url, params=params, data=data, timeout=20)
        txt = r.text
        try:
            js = r.json()
        except Exception:
            js = {"_raw": txt}
        log.info("[ULTRA] resp: %s %s", r.status_code, js)
        return (r.status_code == 200) and (not isinstance(js, dict) or not js.get("error"))
    except Exception as e:
        log.exception("[ULTRA] falha no envio: %s", e)
        return False

def send_email_alert(subject: str, html_body: str) -> bool:
    """Envia e‑mail via SMTP (Gmail App Password)."""
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        log.warning("[EMAIL] SMTP não configurado; skip.")
        return False
    msg = MIMEText(html_body, "html", "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL
    msg["Subject"] = subject
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        log.info("[EMAIL] enviado para %s", ALERT_EMAIL)
        return True
    except Exception as e:
        log.exception("[EMAIL] erro ao enviar: %s", e)
        return False

def build_history(user_text: str):
    """Monta mensagens para a IA (inclui system com persona + regras)."""
    system_msg = {"role": "system", "content": f"{DEFAULT_PERSONA}\n{SYSTEM_RULES}"}
    return [system_msg, {"role": "user", "content": user_text}]

# ====== rotas ======
@app.get("/health")
def health():
    smtp_ready = bool(SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL)
    return jsonify({
        "ultra_instance": INSTANCE_ID,
        "smtp_ready": smtp_ready,
        "ai_provider": "openrouter",
        "silence_hours": SILENCE_FOR_HOURS
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    # UltraMsg envia um JSON com { event_type, data: {...} }
    payload = request.get_json(silent=True) or {}
    log.info("[INBOUND] %s", json.dumps(payload, ensure_ascii=False))

    data = payload.get("data") or {}
    msg_type = (data.get("type") or "").lower()
    body_txt = (data.get("body") or "").strip()
    from_jid = data.get("from") or ""   # cliente
    to_jid   = data.get("to") or ""     # número da instância (modelo)

    client_num = normalize_wa(from_jid)  # ex: "5562..."
    model_num  = normalize_wa(to_jid)

    # bloqueia mídia/áudio e pede texto
    if msg_type in {"ptt", "audio", "voice", "image", "video", "document", "sticker", "location"}:
        send_ultra_text(client_num, "Lindo, aqui só consigo ler mensagem escrita, tá? Me manda por texto que eu te respondo direitinho 💗")
        return jsonify({"ok": True})

    # silêncio ativo?
    now = datetime.utcnow()
    until = silence_until.get(client_num)
    if until and now < until:
        log.info("[SILENT] %s até %s", client_num, until.isoformat())
        return jsonify({"ok": True})

    # história mínima + IA
    history = build_history(body_txt)
    reply = None
    try:
        reply = generate_reply(history, system_prompt=None, model_hint=None)
    except Exception as e:
        log.exception("[AI] erro chamando generate_reply: %s", e)
        reply = ("Amor, me diz: prefere meu local no Villa Rosa (R$300), "
                 "motel ou seu apê (R$500)? E o horário/pagamento (PIX/cartão/dinheiro)?")

    # envia resposta (mantém naturalidade)
    if reply:
        reply = re.sub(r"[\U00010000-\U0010ffff]", "", reply)  # limpa emojis exóticos
        reply = re.sub(r"\s{3,}", "  ", reply).strip()
        send_ultra_text(client_num, reply[:4096])

    # detectar fechamento nesta mesma mensagem do cliente
    intent = detect_booking_intent(body_txt)
    log.info("[INTENT] %s", intent)
    if intent.get("closed"):
        # formata info p/ e‑mail
        place_map = {"meu_local": "meu local (Villa Rosa)", "motel": "motel", "casa_cliente": "casa do cliente"}
        place_txt = place_map.get(intent.get("place") or "", "não identificado")
        when_txt  = " ".join(filter(None, [intent.get("date"), intent.get("time")])) or "não identificado"
        pay_map   = {"pix": "PIX", "dinheiro": "dinheiro", "cartao": "cartão"}
        pay_txt   = pay_map.get(intent.get("pay") or "", "não identificado")

        html = f"""
        <h3>✔ Novo fechamento</h3>
        <p><b>Cliente:</b> +{client_num}</p>
        <p><b>Modelo (instância):</b> +{model_num}</p>
        <p><b>Local:</b> {place_txt}</p>
        <p><b>Quando:</b> {when_txt}</p>
        <p><b>Pagamento:</b> {pay_txt}</p>
        <hr>
        <p><i>reason:</i> {intent.get('reason')}</p>
        """
        send_email_alert("Novo cliente quer marcar", html)
        # silencia 12h
        silence_until[client_num] = now + timedelta(hours=SILENCE_FOR_HOURS)
        log.info("[CLOSED] silenciado %s por %sh", client_num, SILENCE_FOR_HOURS)

    return jsonify({"ok": True})

@app.get("/")
def root():
    return "ok", 200

# ====== main (teste local) ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)