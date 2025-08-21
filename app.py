# app.py
import os
import re
import time
import json
import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import smtplib
from email.mime.text import MIMEText
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ----------------- ENV & LOG -----------------
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

API_URL           = os.getenv("API_URL", "https://api.ultramsg.com")
ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRAMSG_TOKEN    = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")
MY_WA_NUMBER      = (os.getenv("MY_WA_NUMBER") or "").lstrip("+")

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER")
SMTP_PASS   = os.getenv("SMTP_PASS")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

# IA
from ai_provider import generate_reply

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("app")
app = Flask(__name__)

# ----------------- DB (pausas + slots) -----------------
DB_PATH = Path("state.db")

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pauses (
            chat_id TEXT PRIMARY KEY,
            until_ts INTEGER NOT NULL
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            chat_id TEXT PRIMARY KEY,
            local TEXT,
            horario TEXT,
            pagamento TEXT,
            status TEXT,          -- 'collecting' | 'booked'
            last_update INTEGER
        );
    """)
    con.commit()
    return con

def set_pause(chat_id: str, hours: int = 12):
    until = int(time.time() + hours * 3600)
    con = get_db()
    con.execute("INSERT INTO pauses(chat_id, until_ts) VALUES(?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET until_ts=excluded.until_ts",
                (chat_id, until))
    con.commit(); con.close()
    log.info("[PAUSE] %s até %s", chat_id, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until)))

def is_paused(chat_id: str) -> bool:
    try:
        con = get_db()
        row = con.execute("SELECT until_ts FROM pauses WHERE chat_id=?", (chat_id,)).fetchone()
        con.close()
        return bool(row and int(row[0]) > int(time.time()))
    except Exception as e:
        log.warning("pause-check error: %s", e)
        return False

def get_session(chat_id: str) -> Dict[str, Any]:
    con = get_db()
    row = con.execute("SELECT local, horario, pagamento, status, last_update FROM sessions WHERE chat_id=?",
                      (chat_id,)).fetchone()
    con.close()
    if not row:
        return {"local": None, "horario": None, "pagamento": None, "status": "collecting", "last_update": 0}
    return {"local": row[0], "horario": row[1], "pagamento": row[2], "status": row[3], "last_update": row[4]}

def save_session(chat_id: str, sess: Dict[str, Any]):
    con = get_db()
    con.execute("""
        INSERT INTO sessions(chat_id, local, horario, pagamento, status, last_update)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET
          local=excluded.local, horario=excluded.horario,
          pagamento=excluded.pagamento, status=excluded.status,
          last_update=excluded.last_update
    """, (chat_id, sess.get("local"), sess.get("horario"), sess.get("pagamento"),
          sess.get("status"), int(time.time())))
    con.commit(); con.close()

# ----------------- EMAIL -----------------
def send_email(subject: str, body: str, to_addr: Optional[str] = None) -> bool:
    to_addr = to_addr or ALERT_EMAIL
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and to_addr):
        log.warning("[EMAIL] SMTP incompleto ou destino ausente; skip.")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_addr
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [to_addr], msg.as_string())
        log.info("[EMAIL] enviado para %s", to_addr)
        return True
    except Exception as e:
        log.error("[EMAIL] falhou: %s", e)
        return False

# ----------------- ULTRAMSG -----------------
def normalize_jid(s: Optional[str]) -> str:
    if not s: return ""
    return s.split("@")[0].replace(" ", "").lstrip("+")

def ultra_send(to_number_e164: str, text: str) -> bool:
    if not (ULTRA_INSTANCE_ID and ULTRAMSG_TOKEN):
        log.error("[ULTRA] credenciais ausentes")
        return False
    url = f"{API_URL}/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    data = {"to": to_number_e164, "body": text}
    try:
        preview = text if len(text) < 120 else text[:120] + "…"
        log.info("[ULTRA] POST %s?token=*** data=%s", url, {"to": to_number_e164, "body": preview})
        r = requests.post(url, params=params, data=data, timeout=20)
        try:
            j = r.json()
        except Exception:
            j = {"raw": r.text}
        log.info("[ULTRA] resp %s %s", r.status_code, j)
        return r.ok and not j.get("error")
    except Exception as e:
        log.error("[ULTRA] erro envio: %s", e)
        return False

# ----------------- ESTILO (menos emojis) -----------------
EMOJI_REGEX = re.compile(
    "["                                # faixa ampla de emojis
    "\U0001F1E0-\U0001F1FF"            # flags
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+"
)

def strip_emojis(text: str, allow: int = 0) -> str:
    """Remove emojis; se allow>0, mantém até N (primeiros) emojis."""
    if allow <= 0:
        return EMOJI_REGEX.sub("", text)
    # mantém os primeiros N, remove o resto
    kept = 0
    out = []
    for ch in text:
        if EMOJI_REGEX.match(ch):
            if kept < allow:
                out.append(ch); kept += 1
            # senão, descarta
        else:
            out.append(ch)
    return "".join(out)

# ----------------- INTENT & SLOTS -----------------
# locais aceitos
RE_LOCAL  = re.compile(r"\b(villa\s*rosa|motel|apto|apartamento|no\s*meu\s*ap[êe]|no\s*seu\s*ap[êe])\b", re.I)
# horários
RE_HORA   = re.compile(r"\b(\d{1,2}h|\d{1,2}:\d{2}|manh[ãa]|tarde|noite|agora|hoje|amanh[ãa])\b", re.I)
# pagamento
RE_PAGTO  = re.compile(r"\b(pix|cart[aã]o|dinheiro|cr[eé]dito|d[eé]bito)\b", re.I)
# intenção de marcar (não vamos mais pedir confirmação explícita)
RE_MARK   = re.compile(r"\b(quero marcar|vamos marcar|fechar|agendar|marcar)\b", re.I)

def extract_slot_local(text: str) -> Optional[str]:
    m = RE_LOCAL.search(text)
    if not m: return None
    val = m.group(1).lower()
    if "villa" in val:  return "meu local (Villa Rosa)"
    if "motel" in val:  return "motel"
    if "apto" in val or "apart" in val or "seu ap" in val: return "seu apartamento"
    if "meu ap" in val: return "meu local (Villa Rosa)"
    return val

def extract_slot_hora(text: str) -> Optional[str]:
    m = RE_HORA.search(text)
    if not m: return None
    return m.group(1)

def extract_slot_pagto(text: str) -> Optional[str]:
    m = RE_PAGTO.search(text)
    if not m: return None
    g = m.group(1).lower()
    if "pix" in g: return "pix"
    if "cart" in g: return "cartão"
    if "din" in g: return "dinheiro"
    if "cr" in g:  return "cartão"
    if "d" in g:   return "cartão"
    return g

def in_booking_flow(text: str, sess: Dict[str, Any]) -> bool:
    t = (text or "").lower()
    if sess.get("status") in ("collecting", "booked"):
        return True
    if RE_MARK.search(t):  # intenção explícita
        return True
    hits = sum([1 if RE_LOCAL.search(t) else 0,
                1 if RE_HORA.search(t) else 0,
                1 if RE_PAGTO.search(t) else 0])
    return hits >= 2  # sinais fortes

def next_missing(sess: Dict[str, Any]) -> Optional[str]:
    if not sess.get("local"):    return "local"
    if not sess.get("horario"):  return "horário"
    if not sess.get("pagamento"):return "pagamento"
    return None

def price_for_local(local: str) -> str:
    # Gabriele: 300 no próprio local (Villa Rosa), 500 em motel/apto
    if "meu local" in (local or ""): return "R$ 300"
    return "R$ 500"

def make_handoff_message(sess: Dict[str, Any]) -> str:
    """Mensagem curtinha (sem emojis) ao concluir slots, sem pedir confirmação."""
    local = sess.get("local")
    horario = sess.get("horario")
    pagto = sess.get("pagamento")
    valor = price_for_local(local or "")
    # tom simples, coloquial, sem formalidade
    return f"Anotei aqui: {local}, {horario}, {pagto}. Fica {valor}. Te confirmo já por aqui."

# ----------------- HEALTH -----------------
@app.get("/health")
def health():
    smtp_ok = bool(SMTP_USER and SMTP_PASS)
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "smtp_ready": smtp_ok,
        "smtp_missing": [k for k,v in {"SMTP_USER":SMTP_USER,"SMTP_PASS":SMTP_PASS}.items() if not v],
    })

# ----------------- WEBHOOK -----------------
def extract_messages(payload: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    def one(ev: Dict[str, Any]):
        msg = ev.get("data") or ev
        out.append({
            "from": msg.get("from") or msg.get("sender") or msg.get("author") or "",
            "to":   msg.get("to")   or msg.get("chatId") or msg.get("receiver") or "",
            "type": (msg.get("type") or "").lower(),
            "body": (msg.get("body") or (msg.get("text") or {}).get("body") or "").strip(),
            "fromMe": bool(msg.get("fromMe") or msg.get("self") or False),
        })
    if isinstance(payload, list):
        for ev in payload:
            if isinstance(ev, dict): one(ev)
    elif isinstance(payload, dict):
        one(payload)
    return out

@app.post("/ultra-webhook")
def ultra_webhook():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    msgs = extract_messages(payload)

    for m in msgs:
        wa_from = normalize_jid(m["from"])
        wa_to   = normalize_jid(m["to"])
        mtype   = m["type"]
        body    = m["body"]
        from_me = m["fromMe"]

        client_number   = wa_from
        provider_number = wa_to
        chat_id = client_number  # um chat por cliente

        log.info("[INBOUND] prov:%s <- cli:%s | %r (type=%s, fromMe=%s)", provider_number, client_number, body, mtype, from_me)

        # a modelo respondeu manualmente? pausar 12h
        if from_me or (MY_WA_NUMBER and wa_from == MY_WA_NUMBER):
            set_pause(chat_id, 12)
            continue

        # pausado? então ignorar
        if is_paused(chat_id):
            log.info("[PAUSED] %s; ignorando.", chat_id)
            continue

        # apenas texto por enquanto
        if mtype != "chat" or not body:
            continue

        # --------- Fluxo de agendamento (slots, sem confirmação) ---------
        sess = get_session(chat_id)
        if in_booking_flow(body, sess):
            loc = extract_slot_local(body)
            hr  = extract_slot_hora(body)
            pg  = extract_slot_pagto(body)

            if loc: sess["local"] = loc
            if hr:  sess["horario"] = hr
            if pg:  sess["pagamento"] = pg

            missing = next_missing(sess)

            if missing:
                sess["status"] = "collecting"
                save_session(chat_id, sess)
                # perguntas diretas, poucas palavras, sem emojis
                if missing == "local":
                    ultra_send(client_number, "Prefere no meu local no Villa Rosa (R$ 300), em motel ou no seu apê (R$ 500)?")
                elif missing == "horário":
                    ultra_send(client_number, "Qual horário fica melhor pra você? (tipo 20h, noite, amanhã à tarde)")
                else:  # pagamento
                    ultra_send(client_number, "Pagamento você prefere pix, cartão ou dinheiro?")
                continue

            # tem os 3 slots → envia e-mail e pausa 12h, sem pedir confirmação
            sess["status"] = "booked"
            save_session(chat_id, sess)

            resumo = f"Local: {sess['local']}\nHorário: {sess['horario']}\nPagamento: {sess['pagamento']}\nCliente: +{client_number}"
            send_email("Novo agendamento — Gabriele", resumo, ALERT_EMAIL)

            handoff = make_handoff_message(sess)
            handoff = strip_emojis(handoff, allow=0)
            ultra_send(client_number, handoff)
            set_pause(chat_id, 12)
            continue

        # --------- Conversa normal (IA), estilo mais simples ---------
        try:
            reply = generate_reply(body)
        except Exception as e:
            log.error("[AI] erro: %s", e)
            reply = "Me fala o bairro, um horário que te sirva e como prefere pagar (pix, cartão ou dinheiro)."

        # corta emojis (0 permitido) e deixa tom mais direto
        reply = strip_emojis(reply, allow=0).strip()
        ultra_send(client_number, reply[:4096])

    return jsonify({"status":"ok"}), 200

# ----------------- MAIN -----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    log.info("servindo na porta %s", port)
    app.run(host="0.0.0.0", port=port)