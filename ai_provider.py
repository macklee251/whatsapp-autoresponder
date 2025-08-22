# ai_provider.py
import os, re, logging, requests
from pathlib import Path
from dotenv import load_dotenv

log = logging.getLogger("ai")

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

PROVIDER = os.getenv("AI_PROVIDER", "openrouter")
BASE_URL = "https://openrouter.ai/api/v1"
OR_KEY   = os.getenv("OPENROUTER_API_KEY", "")
REPLY_MODEL = os.getenv("REPLY_MODEL", os.getenv("AI_MODEL", "google/gemma-2-9b-it"))

HEADERS = {
    "Authorization": f"Bearer {OR_KEY}" if OR_KEY else "",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://localhost",
    "X-Title": "whatsapp-autoresponder",
}

SYSTEM_RULES = (
    "Fale em PT-BR informal, doce e segura; poucas gírias, sem formalidade excessiva. "
    "No máx. 1 emoji por mensagem, e pode responder sem emoji. "
    "NUNCA invente horário, local, preço, link ou pagamento — pergunte quando necessário. "
    "Nunca ofereça desconto. Recuse menor de idade ou algo ilegal. "
    "Se enviarem áudio/foto/vídeo, diga com educação que só consegue ler texto. "
    "Responda entre 2 e 8 frases (pode ser 1–2 quando apropriado)."
)

def _build_messages(user_text: str, system_persona: str, history=None):
    sys = f"{system_persona}\n\n{SYSTEM_RULES}"
    msgs = [{"role":"system","content":sys}]
    if history:
        msgs.extend(history)  # deve estar no formato [{"role":"user"/"assistant","content":...}, ...]
    msgs.append({"role":"user","content":user_text})
    return msgs

def _call_openrouter(model: str, messages, temperature=0.6, max_tokens=320) -> str:
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    log.info("[OPENROUTER] model=%s", model)
    r = requests.post(f"{BASE_URL}/chat/completions", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _postprocess(txt: str) -> str:
    # remove emojis exóticos e limita repetição
    txt = re.sub(r"[\U00010000-\U0010ffff]", "", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt

def generate_reply(user_text: str, system_persona: str, history=None) -> str:
    """Gera resposta com modelo principal; se falhar, tenta fallback leve."""
    if PROVIDER != "openrouter" or not OR_KEY:
        log.error("[AI] provider não configurado: %s", PROVIDER)
        return "Oi, amor. Me fala se prefere meu local (Villa Rosa), motel ou seu apê — e horário 🙂"

    messages = _build_messages(user_text, system_persona, history)
    pool = [
        REPLY_MODEL,                               # principal do .env
        "qwen/qwen2.5-7b-instruct",               # fallback 1
        "mistralai/mistral-7b-instruct",          # fallback 2
    ]
    last_err = None
    for m in pool:
        try:
            raw = _call_openrouter(m, messages)
            return _postprocess(raw)
        except requests.HTTPError as e:
            last_err = e
            log.warning("[AI] HTTPError model=%s code=%s body=%s",
                        m, getattr(e.response, "status_code", "?"),
                        getattr(e.response, "text", "")[:400])
        except Exception as e:
            last_err = e
            log.warning("[AI] Falha com %s: %s", m, e)
    log.error("[AI] todas as tentativas falharam: %s", last_err)
    return "Quer marcar? Me diz o local (meu local/motel/apê), a hora e pagamento (pix/cartão/dinheiro)."