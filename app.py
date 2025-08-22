import os, time, json, logging, re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests
from smtplib import SMTP
from email.mime.text import MIMEText

# ====== Setup ======
APP_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=APP_DIR / ".env", override=True)

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

ULTRA_BASE   = os.getenv("API_URL", "https://api.ultramsg.com")
ULTRA_INST   = os.getenv("ULTRA_INSTANCE_ID") or os.getenv("INSTANCE_ID")
ULTRA_TOKEN  = os.getenv("ULTRAMSG_TOKEN") or os.getenv("ULTRA_TOKEN")

SMTP_SERVER  = os.getenv("SMTP_SERVER")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER")
SMTP_PASS    = os.getenv("SMTP_PASS")
ALERT_EMAIL  = os.getenv("ALERT_EMAIL")

# janela de não-repetição e silêncio
LOCK_MINUTES   = int(os.getenv("LOCK_MINUTES", "30"))
SILENCE_HOURS  = int(os.getenv("SILENCE_HOURS", "12"))

# Persona fixa (pode ir para DB depois)
PERSONA = {
    "name": "Gabriele",
    "age": 20,
    "style": "sedutora, direta, coloquial, com pequenos deslizes de português; sem formalidade; poucos emojis",
    "location_home": "Villa Rosa",
    "price_home": 300,
    "price_out": 500,
    "pay_methods": ["PIX", "cartão", "dinheiro"],
}

# ====== Memória curta em RAM ======
# Por cliente (jid do WhatsApp), guardamos slots + últimas msgs
STATE = {}  # { client_jid: {last_seen, silence_until, slots:{...}, history:[...]} }

def now_utc():
    return datetime.now(timezone.utc)

def in_future(ts_iso):
    try:
        return datetime.fromisoformat(ts_iso) > now_utc()
    except:
        return False

def get_state(client):
    s = STATE.get(client)
    if not s:
        s = {
            "last_seen": now_utc().isoformat(),
            "silence_until": None,
            "history": [],  # lista de {who: 'client'|'bot', text, t}
            "slots": {
                "location": {"value": None, "ts": None, "lock_until": None},
                "time":     {"value": None, "ts": None, "lock_until": None},
                "payment":  {"value": None, "ts": None, "lock_until": None},
            },
            "closed": False
        }
        STATE[client] = s
    return s

def set_slot(state, key, value):
    if value:
        state["slots"][key]["value"]      = value
        state["slots"][key]["ts"]         = now_utc().isoformat()
        state["slots"][key]["lock_until"] = (now_utc() + timedelta(minutes=LOCK_MINUTES)).isoformat()

def is_locked(state, key):
    lu = state["slots"][key].get("lock_until")
    return bool(lu and in_future(lu))

def clear_if_stale(state):
    """
    Se passaram >12h desde last_seen, limpamos slots e finalizamos silêncios antigos.
    Permite agendamentos em dias distintos sem confusão.
    """
    try:
        last = datetime.fromisoformat(state["last_seen"])
    except:
        last = now_utc() - timedelta(days=1)
    if now_utc() - last > timedelta(hours=12):
        for k in ["location", "time", "payment"]:
            state["slots"][k] = {"value": None, "ts": None, "lock_until": None}
        state["closed"] = False
        state["silence_until"] = None

# ====== UltraMsg ======
def ultra_send_text(to_number, text):
    url = f"{ULTRA_BASE}/{ULTRA_INST}/messages/chat"
    params = {"token": ULTRA_TOKEN}
    data = {"to": to_number, "body": text}
    try:
        r = requests.post(url, params=params, data=data, timeout=20)
        logging.info("[ULTRA] POST %s | %s", r.url, r.status_code)
        try:
            logging.info("[ULTRA] resp json: %s", r.json())
        except Exception:
            logging.info("[ULTRA] resp text: %s", r.text[:400])
        return r.ok
    except Exception as e:
        logging.exception("[ULTRA] erro enviando: %s", e)
        return False

# ====== E-mail no fechamento ======
def send_email_on_close(my_num, client_num, slots):
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        logging.warning("[EMAIL] SMTP não configurado; skip.")
        return
    location = slots["location"]["value"]
    time_txt = slots["time"]["value"]
    payment  = slots["payment"]["value"]
    body = (
        f"Fechamento detectado.\n\n"
        f"Cliente: +{client_num}\n"
        f"Atendente: +{my_num}\n\n"
        f"Local: {location}\n"
        f"Horário/Data: {time_txt}\n"
        f"Pagamento: {payment}\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[Fechou] {client_num} -> {my_num}"
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL

    try:
        with SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        logging.info("[EMAIL] enviado para %s", ALERT_EMAIL)
    except Exception as e:
        logging.exception("[EMAIL] falhou: %s", e)

# ====== NLU simples (regex) + “intent” ======
# (sem IA pesada aqui; depois dá para acoplar ai_intent.py)
RE_LOC = re.compile(r"\b(meu local|no seu local|villa\s*rosa|motel|no meu ap[ê|e]|no seu ap[ê|e])\b", re.I)
RE_PAY = re.compile(r"\b(pix|cart[aã]o|dinheiro)\b", re.I)
# horário/data: aceita “20 h”, “às 20”, “20:30”, “amanhã às 19”, etc.
RE_TIME = re.compile(r"\b(\d{1,2}:\d{2}|\d{1,2}\s*h)|\b(amanh[ãa]|hoje)\b", re.I)

def extract_entities(text):
    found = {}
    if RE_LOC.search(text):
        m = RE_LOC.search(text).group(0).lower()
        # normaliza
        if "villa" in m or "meu local" in m or "seu local" in m:
            found["location"] = f"meu local ({PERSONA['location_home']}, R${PERSONA['price_home']})"
        elif "motel" in m:
            found["location"] = f"motel (R${PERSONA['price_out']})"
        elif "ap" in m:
            found["location"] = f"apê do cliente (R${PERSONA['price_out']})"

    if RE_PAY.search(text):
        found["payment"] = RE_PAY.search(text).group(0).upper()

    # horário/data textual simples
    if RE_TIME.search(text):
        found["time"] = RE_TIME.search(text).group(0)
    return found

def has_closure(state):
    return all(state["slots"][k]["value"] for k in ["location","time","payment"])

# ====== AI (OpenRouter) via seu ai_provider.py ======
# Se você já tem um provider com fallback, importe:
try:
    from ai_provider import generate_reply as llm_reply
except Exception as e:
    logging.warning("[AI] usando modo simples: %s", e)
    def llm_reply(messages, model=None, temperature=0.6, max_tokens=220):
        # fallback bem simples para não quebrar.
        return "Tá bom, amor. Me diz onde prefere (meu local, motel ou seu apê) e como quer pagar (PIX, cartão ou dinheiro)."

# reforço de personagem e proibição de “sou um assistente”
FORBIDDEN_PATTERNS = [
    r"\bsou (um|uma) (assistente|ia|rob[oô])\b",
    r"\bsou (apenas|s[oó]) (um|uma) modelo de linguagem\b",
    r"\bcomo assistente\b",
]

def in_character_repair(text):
    low = text.lower()
    if any(re.search(p, low) for p in FORBIDDEN_PATTERNS):
        # segunda chance: uma resposta curta em personagem
        return ("Sou a Gabriele, amor. Vamos direto: "
                "me diz o lugar (meu local, motel ou teu apê) e como prefere pagar (PIX, cartão ou dinheiro).")
    return text

def build_system_prompt(state):
    # monta instruções com o que já temos/falta
    have = []
    miss = []
    for k, label in [("location","local"),("time","horário"),("payment","pagamento")]:
        if state["slots"][k]["value"]:
            have.append(label)
        else:
            miss.append(label)

    base = f"""
Você é **Gabriele** (20 anos), magrinha, loira, 1,60m, atende no **Villa Rosa** (R${PERSONA['price_home']}) ou **motel/apê do cliente** (R${PERSONA['price_out']}).
Seu tom é **natural, sedutor, direto e coloquial**, com pequenos deslizes de português. Poucos emojis. Nunca negocie desconto.

Regras:
- Jamais diga que é assistente, IA ou modelo de linguagem. Você é a Gabriele.
- Se o cliente mandar **áudio/foto/vídeo**, diga educadamente que só consegue ler texto.
- Se faltar **apenas um** dos itens (local/horário/pagamento), **pergunte só o que falta** sem repetir os outros.
- Se já tivermos os 3 itens, **não confirme nada**: apenas finalize com uma frase curta, calorosa e segura.
- Evite repetir a mesma pergunta dentro de {LOCK_MINUTES} minutos se já foi respondida.

Já temos: {', '.join(have) if have else 'nada'}
Falta: {', '.join(miss) if miss else 'nada'}
"""
    return base.strip()

# ====== Rotas ======
@app.route("/health")
def health():
    return jsonify({
        "ai_provider": os.getenv("AI_PROVIDER"),
        "ai_model": os.getenv("AI_MODEL"),
        "smtp_ready": bool(SMTP_SERVER and SMTP_USER and SMTP_PASS and ALERT_EMAIL),
        "ultra_instance": ULTRA_INST,
    })

@app.route("/ultra-webhook", methods=["POST"])
def ultra_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    logging.info("[INBOUND] %s", payload)
    data = payload.get("data") or {}

    # ignore “fromMe=true” (mensagens enviadas pela modelo manualmente)
    if data.get("fromMe"):
        client = (data.get("to") or "").replace("@c.us","").replace("+","")
        st = get_state(client)
        st["silence_until"] = (now_utc() + timedelta(hours=SILENCE_HOURS)).isoformat()
        logging.info("[FLOW] modelo respondeu; silenciando %sh para %s", SILENCE_HOURS, client)
        return jsonify({"status":"ok"}), 200

    # apenas chat de texto
    if (data.get("type") or "").lower() != "chat":
        client = (data.get("from") or "").replace("@c.us","").replace("+","")
        ultra_send_text(client, "Amor, consigo ler só mensagens escritas, tá? Me manda em texto, por favor.")
        return jsonify({"status":"ok"}), 200

    # normaliza cliente e texto
    client = (data.get("from") or "").replace("@c.us","").replace("+","")
    text = (data.get("body") or "").strip()

    # estado e janelas
    st = get_state(client)
    clear_if_stale(st)
    st["last_seen"] = now_utc().isoformat()
    if st.get("silence_until") and in_future(st["silence_until"]):
        logging.info("[FLOW] silenciado até %s; ignorando", st["silence_until"])
        return jsonify({"status":"ok"}), 200

    # memoriza histórico curto
    st["history"].append({"who":"client", "text":text, "t": st["last_seen"]})
    st["history"] = st["history"][-12:]

    # extrair entidades simples
    found = extract_entities(text)
    for k, v in found.items():
        set_slot(st, k, v)

    # fechamento?
    if has_closure(st):
        if not st.get("closed"):
            st["closed"] = True
            send_email_on_close(data.get("to",""), client, st["slots"])
            # envia mensagem final e silencia 12h
            ultra_send_text(client, "Perfeito, amor. Fico te esperando. 💋")
            st["silence_until"] = (now_utc() + timedelta(hours=SILENCE_HOURS)).isoformat()
            logging.info("[FLOW] FECHADO → email enviado e silenciei %sh", SILENCE_HOURS)
        # mesmo que já estivesse fechado, não responde mais
        return jsonify({"status":"ok"}), 200

    # decidir o “falta o quê” respeitando lock
    needs = []
    for k,label in [("location","local"),("time","horário"),("payment","pagamento")]:
        if not st["slots"][k]["value"] and not is_locked(st,k):
            needs.append(label)

    # Se nada “em falta” (porque tudo está lockado por 30min, p.ex.), faça uma resposta leve de continuidade
    if not needs:
        sys_prompt = build_system_prompt(st)
        messages = [
            {"role":"system","content": sys_prompt},
            {"role":"user","content": text}
        ]
        reply = in_character_repair(llm_reply(messages, temperature=0.6, max_tokens=220))
        ultra_send_text(client, reply)
        st["history"].append({"who":"bot","text":reply,"t":now_utc().isoformat()})
        return jsonify({"status":"ok"}), 200

    # Monta prompt pedindo SÓ o que falta
    ask_bits = []
    if "local" in needs:
        ask_bits.append(f"Pergunte onde prefere: meu local no {PERSONA['location_home']} (R${PERSONA['price_home']}), motel, ou no apê dele (R${PERSONA['price_out']}).")
    if "horário" in needs:
        ask_bits.append("Pergunte de forma natural qual horário ele quer.")
    if "pagamento" in needs:
        ask_bits.append(f"Pergunte como prefere pagar ({', '.join(PERSONA['pay_methods'])}).")

    sys_prompt = build_system_prompt(st) + "\n" + " ".join(ask_bits)
    messages = [
        {"role":"system","content": sys_prompt},
        {"role":"user","content": text}
    ]
    resp = llm_reply(messages, temperature=0.7, max_tokens=220)
    reply = in_character_repair(resp)

    ultra_send_text(client, reply)
    st["history"].append({"who":"bot","text":reply,"t":now_utc().isoformat()})
    return jsonify({"status":"ok"}), 200

# ====== main (dev) ======
if __name__ == "__main__":
    print("SMTP_USER:", SMTP_USER)
    app.run(host="0.0.0.0", port=8000)