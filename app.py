# --- adicione/imports no topo (se ainda não tiver) ---
import os, time, random
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
import requests

from ai_provider import generate_reply  # já usamos antes

app = Flask(__name__)

ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID")
ULTRAMSG_TOKEN    = os.getenv("ULTRAMSG_TOKEN")

# janela de atraso “humano”
DELAY_MIN = int(os.getenv("DELAY_MIN_SECONDS", "40"))   # 40s
DELAY_MAX = int(os.getenv("DELAY_MAX_SECONDS", "150"))  # 2m30s

# quantidade de workers de background
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "4")))

def send_text(to_number: str, body: str) -> bool:
    """Envia texto via UltraMsg."""
    try:
        url = f"https://api.ultramsg.com/{ULTRA_INSTANCE_ID}/messages/chat"
        r = requests.post(url, params={"token": ULTRAMSG_TOKEN},
                          data={"to": to_number, "body": body}, timeout=15)
        ok = r.ok and (r.json().get("sent") or r.json().get("status") in ("sent","ok", True))
        print(f"[ULTRA] -> {to_number}: '{body[:80]}' | HTTP {r.status_code} | ok={ok}")
        return bool(ok)
    except Exception as e:
        print(f"[ULTRA][ERRO] {e}")
        return False

def _normalize_wa(num: str) -> str:
    """Normaliza números para E.164 sem sinais, ex: +5562... ou 5562... -> 5562..."""
    if not num:
        return ""
    n = "".join(ch for ch in str(num) if ch.isdigit() or ch == '+')
    return n.lstrip('+')

def process_with_ai(client_number: str, user_text: str):
    """Roda depois do OK: espera atraso, gera resposta e envia."""
    try:
        delay = random.randint(DELAY_MIN, DELAY_MAX)
        print(f"[FLOW] aguardando {delay}s antes da IA…")
        time.sleep(delay)

        # persona padrão vem do .env se existir
        persona = os.getenv("DEFAULT_PERSONA", "")
        reply = generate_reply(user_text, persona=persona)
        if not reply:
            reply = "Certo, amor! 😊 Me diz bairro e a faixa de horário (manhã/tarde/noite) pra eu confirmar pra você."

        send_text(client_number, reply[:4096])
    except Exception as e:
        print(f"[AI][ERRO BACKGROUND] {e}")

@app.post("/ultra-webhook")
def ultra_webhook():
    """Webhook da UltraMsg: responde OK na hora e dispara IA em background."""
    data = request.get_json(silent=True, force=True) or {}
    # Estrutura comum da UltraMsg:
    # data.get('type') == 'chat', data.get('from'), data.get('to'), data.get('body')
    mtype = (data.get("type") or "").lower()
    body  = (data.get("body") or "").strip()
    wa_from = _normalize_wa(data.get("from"))
    print(f"[INBOUND] type={mtype} from={wa_from} body='{body}'")

    # só tratamos mensagens de chat com texto
    if mtype != "chat" or not body or not wa_from:
        return jsonify({"status": "ignored"}), 200

    # 1) responde “Ok” imediatamente
    send_text(wa_from, "Ok")

    # 2) agenda IA em segundo plano (não bloqueia o webhook)
    EXECUTOR.submit(process_with_ai, wa_from, body)

    # 3) responde 200 rapidamente para o provedor não re‑tentar
    return jsonify({"status": "queued"}), 200