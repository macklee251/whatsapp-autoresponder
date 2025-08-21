# app.py
import os
import re
import json
import time
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ===== env =====
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

API_URL            = os.getenv("API_URL", "https://api.ultramsg.com")
ULTRA_INSTANCE_ID  = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRAMSG_TOKEN     = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "mlee251@icloud.com")

# IA (openrouter) é carregada no ai_provider.py
from ai_provider import generate_reply  # usa pool/fallback que você já configurou

# ===== flask =====
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

# ===== memória simples de sessão por cliente =====
SESS = {}  # phone -> dict
SILENCE = {}  # phone -> datetime until

SILENCE_HOURS = 12

# ===== util =====
def normalize_wa(jid: str) -> str:
    # "5562...@c.us" -> "+5562..."
    if not jid:
        return ""
    num = jid.split("@")[0]
    if not num.startswith("+"):
        num = "+" + num
    return num

def silent_until(phone: str) -> bool:
    until = SILENCE.get(phone)
    if not until:
        return False
    return datetime.utcnow() < until

def set_silence(phone: str, hours=SILENCE_HOURS):
    SILENCE[phone] = datetime.utcnow() + timedelta(hours=hours)

def strip_emojis(txt: str, allow=0) -> str:
    # remove a maioria dos emojis; permite alguns se quiser
    # aqui vamos deixar quase sem
    return re.sub(r"[\U00010000-\U0010ffff]", "", txt)

# ===== UltraMsg =====
def ultra_send_text(to_number: str, text: str) -> bool:
    """
    Envia mensagem pelo UltraMsg.
    OBS: token deve ir na query string (?token=...), NÃO no corpo.
    """
    if not (ULTRA_INSTANCE_ID and ULTRAMSG_TOKEN):
        log.error("[ULTRA] credenciais ausentes")
        return False

    url = f"{API_URL}/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    data = {"to": to_number.replace("+",""), "body": text}

    try:
        r = requests.post(url, params=params, data=data, timeout=20)
        log.info("[ULTRA] URL: %s?token=***", url)
        ok = (r.status_code == 200)
        body = {}
        try:
            body = r.json()
        except Exception:
            body = {"_raw": r.text}
        log.info("[ULTRA] resp: %s %s", r.status_code, body)
        return ok and not body.get("error")
    except Exception as e:
        log.exception("[ULTRA] erro ao enviar: %s", e)
        return False

# ===== email (SMTP) =====
def send_email_alert(subject: str, body: str) -> bool:
    """envia e‑mail simples via SMTP; ignora se SMTP_* faltando"""
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        log.warning("[EMAIL] SMTP não configurado; skip.")
        return False
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        log.info("[EMAIL] enviado para %s", ALERT_EMAIL)
        return True
    except Exception as e:
        log.exception("[EMAIL] erro: %s", e)
        return False

# ===== extração de intenção e slots =====
RE_LOCAL  = re.compile(r"\b(villa\s*rosa|meu\s*local|motel|ap[êe]|apartamento|ape|no\s*meu\s*ap[êe])\b", re.I)
RE_HORA   = re.compile(r"\b(\d{1,2})h\b|\b(\d{1,2}:\d{2})\b|\b(hoje|amanh[aã])\b", re.I)
RE_PAGTO  = re.compile(r"\b(pix|dinheiro|cart[aã]o)\b", re.I)

def extract_slot_local(text: str) -> str | None:
    m = RE_LOCAL.search(text or "")
    if not m: return None
    v = m.group(0).lower()
    if "motel" in v:
        return "motel"
    if "ap" in v or "apart" in v:
        return "cliente"
    if "villa" in v or "meu local" in v:
        return "meu_local"
    return v

def extract_slot_hora(text: str) -> str | None:
    t = text or ""
    m = RE_HORA.search(t)
    if not m: return None
    g = [x for x in m.groups() if x]
    return g[0] if g else None

def extract_slot_pagto(text: str) -> str | None:
    m = RE_PAGTO.search(text or "")
    return m.group(0).lower() if m else None

def looks_like_media(body: dict) -> bool:
    t = (body or {}).get("type","")
    return t in {"image","video","ptt","audio","document","ptv","sticker","location","vcard"}

def polite_media_reply() -> str:
    return "amor, n consigo ouvir áudio nem abrir mídia por aqui… me manda por texto? 💕"

# ===== webhook =====
@app.route("/ultra-webhook", methods=["POST"])
def ultra_webhook():
    payload = request.get_json(silent=True) or {}
    log.info("[INBOUND] %s", json.dumps(payload, ensure_ascii=False))

    data = payload.get("data") or {}
    from_jid = data.get("from", "")
    to_jid   = data.get("to", "")
    body_txt = data.get("body", "") or ""
    msg_type = data.get("type", "chat")
    from_me  = bool(data.get("fromMe"))

    client_number = normalize_wa(from_jid)
    modelo_number = normalize_wa(to_jid)

    # se a modelo respondeu manualmente -> silenciar 12h
    if from_me:
        if client_number:
            set_silence(client_number, SILENCE_HOURS)
        return jsonify({"ok": True})

    # filtra mídia (educado)
    if msg_type != "chat" or looks_like_media(data):
        reply = polite_media_reply()
        ultra_send_text(client_number, reply)
        return jsonify({"ok": True})

    # se está silenciado (já fechou ou modelo respondeu)
    if silent_until(client_number):
        log.info("[SILENT] %s ainda em janela de silêncio", client_number)
        return jsonify({"ok": True})

    # === 1) sempre pede resposta da IA primeiro (naturalidade) ===
    try:
        ai_reply = generate_reply(
            user_text=body_txt,
            system_persona=os.getenv("DEFAULT_PERSONA", "Você é Gabriele..."),
            # opcional: passar histórico básico desta sessão (mantemos curto)
            history=SESS.get(client_number, {}).get("history", [])[-6:],
        )
    except Exception as e:
        log.exception("[AI] falhou; usando fallback simples: %s", e)
        ai_reply = "oii :) me fala se vc prefere no meu local (R$ 300), motel ou no seu apê (R$ 500), e o horário…"

    # limita/ajusta estilo: menos emoji e sem formalidade excessiva
    ai_reply = strip_emojis(ai_reply, allow=0)
    ai_reply = re.sub(r"\s{3,}", "  ", ai_reply).strip()
    ai_reply = ai_reply[:4096]

    # guarda um mini-histórico pra IA “lembrar” um pouco
    h = SESS.setdefault(client_number, {}).setdefault("history", [])
    h.append({"role":"user","content":body_txt})
    h.append({"role":"assistant","content":ai_reply})
    h[:] = h[-10:]  # janela curta

    # === 2) tenta identificar agendamento (no texto DO CLIENTE) ===
    loc = extract_slot_local(body_txt)
    hr  = extract_slot_hora(body_txt)
    pg  = extract_slot_pagto(body_txt)

    if loc and hr and pg:
        # formata valores/custos
        if loc == "meu_local":
            preco = 300
            local_txt = "no meu local (Villa Rosa)"
        elif loc == "motel":
            preco = 500
            local_txt = "em motel"
        else:
            preco = 500
            local_txt = "no seu apê"

        # manda e‑mail para a modelo
        assunto = "🛎️ Novo agendamento (bot)"
        corpo = (
            f"Cliente: {client_number}\n"
            f"Local: {local_txt}\n"
            f"Horário: {hr}\n"
            f"Pagamento: {pg}\n"
            f"Valor estimado: R$ {preco}\n"
        )
        send_email_alert(assunto, corpo)

        # responde curto e silencia 12h (modelo assume a conversa)
        confirm_txt = "fechadinho 💋 te espero então; qualquer coisa a gente fala lá. (a modelo assume daqui)"
        ultra_send_text(client_number, confirm_txt)
        set_silence(client_number, SILENCE_HOURS)
        log.info("[CLOSED] %s agendado -> silêncio por 12h", client_number)
        return jsonify({"ok": True})

    # === 3) se não fechou, envia a resposta da IA ===
    ultra_send_text(client_number, ai_reply)
    return jsonify({"ok": True})

# ===== health =====
@app.route("/health")
def health():
    miss = []
    for k in ("ULTRA_INSTANCE_ID","ULTRAMSG_TOKEN"):
        if not globals().get(k):
            miss.append(k)
    smtp_ok = bool(SMTP_SERVER and SMTP_USER and SMTP_PASS)
    ai_info = {"ai_provider": os.getenv("AI_PROVIDER","openrouter"),
               "ai_model": os.getenv("AI_MODEL","(pool)")}
    return jsonify({
        **ai_info,
        "ultra_instance": ULTRA_INSTANCE_ID,
        "smtp_ready": smtp_ok,
        "smtp_missing": [] if smtp_ok else ["SMTP_SERVER","SMTP_USER","SMTP_PASS"],
        "env_missing": miss
    })

# ===== main =====
if __name__ == "__main__":
    # dev-mode: roda o flask direto; em produção use gunicorn
    log.info("UltraMsg Instance: %s", ULTRA_INSTANCE_ID)
    log.info("SMTP USER: %s", SMTP_USER)
    app.run(host="0.0.0.0", port=8000)