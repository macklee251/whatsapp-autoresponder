# app.py
import os, re, json, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests
from smtplib import SMTP
from email.mime.text import MIMEText

from ai_intent import detect_intent_and_fill_state
from ai_provider import generate_reply

# ========= Setup / ENV =========
APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

API_URL            = os.getenv("API_URL", "https://api.ultramsg.com").rstrip("/")
ULTRA_INSTANCE_ID  = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")  # ex: instance139762
ULTRAMSG_TOKEN     = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER", "")
SMTP_PASS   = os.getenv("SMTP_PASS", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")

LOCK_MINUTES  = int(os.getenv("LOCK_MINUTES", "30"))   # janela sem repetir pedido já atendido
SILENCE_HOURS = int(os.getenv("SILENCE_HOURS", "12"))  # silêncio após fechar

# Persona base
PERSONA = {
    "name": "Gabriele",
    "age": 20,
    "style": "sedutora, direta, coloquial; poucos emojis; erros leves aceitáveis",
    "home_area": "Villa Rosa",
    "price_home": 300,
    "price_out": 500,
    "pay_methods": ["PIX", "cartão", "dinheiro"],
}

# ========= Memória curta (em RAM) =========
STATE = {}  # { client_number: {"last_seen":iso, "silence_until":iso|None, "closed":bool, "history":[...], "slots":{...}} }

def now_utc():
    return datetime.now(timezone.utc)

def parse_iso(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def is_future(ts_iso):
    dt = parse_iso(ts_iso) if isinstance(ts_iso, str) else ts_iso
    return bool(dt and dt > now_utc())

def normalize_wa(n: str) -> str:
    n = (n or "").replace("@c.us", "").replace("+", "")
    n = re.sub(r"\D+", "", n)
    return n

def get_state(client: str):
    s = STATE.get(client)
    if not s:
        s = {
            "last_seen": now_utc().isoformat(),
            "silence_until": None,
            "closed": False,
            "history": [],  # [{role:'user'|'assistant', content:'...'}] — manter curto
            "slots": {
                "location": {"value": None, "lock_until": None},
                "time":     {"value": None, "lock_until": None},
                "payment":  {"value": None, "lock_until": None},
            }
        }
        STATE[client] = s
    return s

def push_history(state, role, content):
    if content:
        state["history"].append({"role": role, "content": content})
        state["history"] = state["history"][-16:]

def set_slot(state, key, val):
    if not val: return
    state["slots"][key]["value"] = val
    state["slots"][key]["lock_until"] = (now_utc() + timedelta(minutes=LOCK_MINUTES)).isoformat()

def clear_stale_if_needed(state):
    """Se passou mais de 12h sem falar, limpa slots/fechamento/silêncio."""
    last = parse_iso(state.get("last_seen"))
    if not last:
        last = now_utc() - timedelta(days=1)
    if now_utc() - last > timedelta(hours=12):
        for k in ("location","time","payment"):
            state["slots"][k] = {"value": None, "lock_until": None}
        state["closed"] = False
        state["silence_until"] = None

def slot_locked(state, key):
    lu = state["slots"][key].get("lock_until")
    return is_future(lu)

def has_all_slots(state) -> bool:
    sl = state["slots"]
    return bool(sl["location"]["value"] and sl["time"]["value"] and sl["payment"]["value"])

# ========= UltraMsg =========
def ultra_send_text(to_number: str, text: str) -> bool:
    if not (ULTRA_INSTANCE_ID and ULTRAMSG_TOKEN):
        log.error("[ULTRA] credenciais ausentes")
        return False
    url = f"{API_URL}/{ULTRA_INSTANCE_ID}/messages/chat"
    params = {"token": ULTRAMSG_TOKEN}
    to = to_number if to_number.startswith("+") else f"+{to_number}"
    data = {"to": to, "body": text}
    try:
        r = requests.post(url, params=params, data=data, timeout=20)
        log.info("[ULTRA] %s -> %s", r.status_code, r.text[:300])
        try:
            j = r.json()
            if isinstance(j, dict) and j.get("error"):
                log.error("[ULTRA] erro lógico: %s", j)
                return False
        except Exception:
            pass
        return r.status_code == 200
    except Exception as e:
        log.exception("[ULTRA] envio falhou: %s", e)
        return False

# ========= Email (fechamento) =========
def send_email_on_close(model_number: str, client_number: str, state) -> bool:
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        log.warning("[EMAIL] SMTP não configurado; skip.")
        return False
    loc = state["slots"]["location"]["value"]
    tm  = state["slots"]["time"]["value"]
    pg  = state["slots"]["payment"]["value"]
    if loc == "meu_local":
        local_txt = f"meu local ({PERSONA['home_area']})"
        preco = PERSONA["price_home"]
    elif loc == "motel":
        local_txt = "motel"
        preco = PERSONA["price_out"]
    else:
        local_txt = "apê do cliente"
        preco = PERSONA["price_out"]

    body = (
        f"Fechamento detectado.\n\n"
        f"Cliente: +{client_number}\n"
        f"Atendente (número): +{model_number}\n"
        f"Local: {local_txt}\n"
        f"Horário/Data: {tm}\n"
        f"Pagamento: {pg}\n"
        f"Valor estimado: R$ {preco}\n"
        f"Registro: {now_utc().isoformat()}\n"
    )
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = f"[Agendamento] {client_number} -> {model_number}"
    msg["From"] = SMTP_USER
    msg["To"]   = ALERT_EMAIL
    try:
        with SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        log.info("[EMAIL] enviado para %s", ALERT_EMAIL)
        return True
    except Exception as e:
        log.exception("[EMAIL] erro: %s", e)
        return False

# ========= Persona & Prompt =========
FORBIDDEN_PATTERNS = [
    r"\b(sou|sou apenas|como) (um|uma)?\s*(assistente|ia|rob[oô]|modelo de linguagem)\b",
]

def in_character_guard(text: str) -> str:
    low = text.lower()
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, low):
            # substitui por uma frase “em personagem”
            return "Sou a Gabriele, amor. Me fala só o que falta: local (meu local/motel/teu apê), horário e como prefere pagar (PIX, cartão ou dinheiro)."
    return text

def system_persona_for(state) -> str:
    have = []
    miss = []
    for k, label in (("location","local"),("time","horário"),("payment","pagamento")):
        if state["slots"][k]["value"]:
            have.append(label)
        else:
            # se está “travado” por lock, consideramos “temporalmente já atendido”
            if slot_locked(state, k):
                have.append(label)
            else:
                miss.append(label)

    base = (
        f"Você é **Gabriele** (20), magrinha, loira, 1,60m; atende no **{PERSONA['home_area']}** (R${PERSONA['price_home']}) "
        f"ou **motel/apê do cliente** (R${PERSONA['price_out']}). "
        f"Tom: natural, sedutor, direto, coloquial; poucos emojis; erros leves OK. "
        f"Nunca ofereça/aceite desconto. Proibido menor/ilegal. "
        f"Se enviarem mídia/áudio, diga que só consegue ler texto. "
        f"Seu objetivo é conduzir até os 3 pontos: local + horário + pagamento. "
        f"Não peça confirmação quando já tiver os 3. "
        f"Evite repetir pergunta sobre item já informado nos últimos {LOCK_MINUTES} minutos.\n\n"
        f"Já temos: {', '.join(have) if have else 'nada'}\n"
        f"Falta: {', '.join(miss) if miss else 'nada'}"
    )
    return base

# ========= Rotas =========
@app.get("/health")
def health():
    return jsonify({
        "ultra_instance": ULTRA_INSTANCE_ID,
        "smtp_ready": bool(SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL),
        "lock_minutes": LOCK_MINUTES,
        "silence_hours": SILENCE_HOURS
    })

@app.post("/ultra-webhook")
def ultra_webhook():
    payload = request.get_json(silent=True) or {}
    log.info("[INBOUND] %s", json.dumps(payload, ensure_ascii=False))
    data = payload.get("data") or {}

    # de/para
    from_jid = data.get("from", "")
    to_jid   = data.get("to", "")
    msg_type = (data.get("type") or "").lower()
    body_txt = (data.get("body") or "").strip()
    from_me  = bool(data.get("fromMe"))

    client_number = normalize_wa(from_jid)   # quem enviou
    model_number  = normalize_wa(to_jid)     # nosso número (da modelo)

    # se a modelo respondeu manualmente → silenciar 12h
    if from_me:
        st = get_state(client_number)
        st["silence_until"] = (now_utc() + timedelta(hours=SILENCE_HOURS)).isoformat()
        log.info("[FLOW] modelo falou; silêncio %dh para %s", SILENCE_HOURS, client_number)
        return jsonify({"ok": True})

    # só texto
    if msg_type != "chat" or not body_txt:
        ultra_send_text(client_number, "Amor, consigo ver só mensagens escritas por aqui, tá? Manda em texto pra mim 💗")
        return jsonify({"ok": True})

    st = get_state(client_number)
    clear_stale_if_needed(st)
    st["last_seen"] = now_utc().isoformat()

    # silêncio ativo?
    if st.get("silence_until") and is_future(st["silence_until"]):
        log.info("[FLOW] em silêncio até %s", st["silence_until"])
        return jsonify({"ok": True})

    # histórico (para IA ter contexto curto)
    push_history(st, "user", body_txt)

    # 1) atualizar slots via intent leve
    st_slots_before = json.dumps(st["slots"], ensure_ascii=False)
    st.update({"state": detect_intent_and_fill_state({
        "location": st["slots"]["location"]["value"],
        "time":     st["slots"]["time"]["value"],
        "payment":  st["slots"]["payment"]["value"],
        "closed":   st.get("closed", False),
        "silence_until": st.get("silence_until")
    }, body_txt)})  # não usamos 'state' adiante; só queremos o 'closed' derivado
    # aplicar de volta nos slots a partir do 'state'
    new = st["state"]
    if new.get("location") and not st["slots"]["location"]["value"]:
        set_slot(st, "location", new["location"])
    if new.get("time") and not st["slots"]["time"]["value"]:
        set_slot(st, "time", new["time"])
    if new.get("payment") and not st["slots"]["payment"]["value"]:
        set_slot(st, "payment", new["payment"])

    # 2) fechou? (temos os 3)
    if has_all_slots(st):
        if not st.get("closed"):
            st["closed"] = True
            # email + mensagem final + silêncio
            send_email_on_close(model_number, client_number, st)
            ultra_send_text(client_number, "Perfeito, amor. Fico te esperando. 💋")
            st["silence_until"] = (now_utc() + timedelta(hours=SILENCE_HOURS)).isoformat()
            log.info("[FLOW] FECHADO -> silenciei %dh", SILENCE_HOURS)
        return jsonify({"ok": True})

    # 3) montar persona dinâmica e perguntar só o que falta
    needs = []
    for key, label in (("location","local"),("time","horário"),("payment","pagamento")):
        if not st["slots"][key]["value"] and not slot_locked(st, key):
            needs.append(label)

    persona = system_persona_for(st)
    prompt_hint = []
    if "local" in needs:
        prompt_hint.append(f"Pergunte onde prefere: meu local em {PERSONA['home_area']} (R${PERSONA['price_home']}), motel ou o apê dele (R${PERSONA['price_out']}).")
    if "horário" in needs:
        prompt_hint.append("Pergunte de forma natural o horário ideal.")
    if "pagamento" in needs:
        prompt_hint.append(f"Pergunte a forma de pagamento ({', '.join(PERSONA['pay_methods'])}).")

    system_persona = persona + ("\n" + " ".join(prompt_hint) if prompt_hint else "")

    reply_raw = generate_reply(
        user_text=body_txt,
        system_persona=system_persona,
        history=st["history"]
    )
    reply = in_character_guard(reply_raw)
    ultra_send_text(client_number, reply)
    push_history(st, "assistant", reply)

    return jsonify({"ok": True})

# ========= main =========
if __name__ == "__main__":
    log.info("SMTP_USER: %s", SMTP_USER)
    app.run(host="0.0.0.0", port=8000)