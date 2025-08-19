import os
import hashlib
import hmac
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests

load_dotenv()

ULTRA_INSTANCE_ID = os.getenv("ULTRA_INSTANCE_ID")
ULTRA_TOKEN = os.getenv("ULTRA_TOKEN")
ULTRA_BASE = f"https://api.ultramsg.com/{ULTRA_INSTANCE_ID}"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # opcional

if not ULTRA_INSTANCE_ID or not ULTRA_TOKEN:
    raise SystemExit("Defina ULTRA_INSTANCE_ID e ULTRA_TOKEN no .env")

ULTRA_BASE = f"https://api.ultramsg.com/{ULTRA_INSTANCE_ID}"

app = Flask(__name__)

# memória volátil simples pra evitar responder duas vezes a mesma msg
SEEN = set()

def normalize_from(wa_from: str) -> str:
    # "5562....@c.us" -> "5562...."
    return wa_from.split("@")[0]

def send_text(to_number_e164: str, text: str) -> bool:
    url = f"{ULTRA_BASE}/messages/chat"
    data = {
        "token": ULTRA_TOKEN,
        "to": to_number_e164,
        "body": text
    }
    try:
        r = requests.post(url, data=data, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print("Falha ao enviar:", e)
        try:
            print("Resp:", r.text)
        except:
            pass
        return False

def verify_signature(raw_body: bytes) -> bool:
    """
    Se o UltraMsg permitir configurar um secret e enviar assinatura no header,
    valide aqui. Exemplo genérico (ajuste o nome do header/algoritmo conforme o provedor):
    """
    if not WEBHOOK_SECRET:
        return True  # sem secret configurado, pula validação
    sig = request.headers.get("X-Ultramsg-Signature")
    if not sig:
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)

@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@app.route("/ultra-webhook", methods=["POST"])
def ultra_webhook():
    raw = request.get_data()
    if not verify_signature(raw):
        return "invalid signature", 403

    data = request.get_json(silent=True) or {}
    # O UltraMsg pode enviar um array de eventos ou um único objeto
    events = data if isinstance(data, list) else [data]

    for ev in events:
        # formatações variam; trate os campos defensivamente:
        msg = (
            ev.get("data") or ev.get("message") or ev
        )  # algumas versões aninham em "data" ou "message"

        # ignore mensagens que nós mesmos enviamos
        from_me = msg.get("fromMe") or msg.get("self")
        if from_me:
            continue

        # pegue um id único da mensagem para idempotência
        msg_id = msg.get("id") or msg.get("messageId") or (msg.get("key") or {}).get("id")
        if not msg_id or msg_id in SEEN:
            continue
        SEEN.add(msg_id)

        # corpo do texto
        text = None
        if "body" in msg:
            text = msg["body"]
        elif isinstance(msg.get("text"), dict):
            text = msg["text"].get("body")
        if not text:
            continue  # protótipo Etapa 1: só texto

        # origem do contato
        wa_from = msg.get("from") or msg.get("chatId") or (msg.get("sender") or {}).get("id")
        if not wa_from:
            continue
        to_number = normalize_from(wa_from)

        print(f"-> De {to_number}: {text!r}")

        # resposta fixa
        reply = "Olá! Recebi sua mensagem ✅"
        ok = send_text(to_number, reply)
        print(f"<- Resposta para {to_number}: {'OK' if ok else 'ERRO'}")

    # Sempre retorne 200 rapidamente para o provedor não re-tentar
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # No Codespaces, a porta publicada aparecerá automaticamente.
    app.run(host="0.0.0.0", port=8000)