# ai_provider.py
import os, re, json, time, logging, requests
from pathlib import Path
from dotenv import load_dotenv

log = logging.getLogger("ai")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

# Pool de modelos (ordem = preferência); pode ajustar no .env via AI_MODEL/REPLY_MODEL se quiser
MODEL_POOL = [
    os.getenv("REPLY_MODEL") or os.getenv("AI_MODEL") or "google/gemma-2-9b-it",
    "qwen/qwen2.5-7b-instruct",
    "mistralai/mistral-7b-instruct",
]

SYSTEM_RULES = (
    "Fale em PT-BR coloquial, natural e sedutor; poucos emojis (0–1). "
    "Jamais diga que é IA/assistente/modelo de linguagem: você é a pessoa. "
    "Nunca invente horário/preço/link; não ofereça desconto. "
    "Se pedirem áudio/foto/vídeo, diga educadamente que só consegue ler texto. "
    "Responda entre 2 e 8 frases (curta quando apropriado)."
)

def _headers():
    return {
        "Authorization": f"Bearer {OR_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost",
        "X-Title": "whatsapp-autoresponder",
    }

def _merge_messages(system_persona: str, history=None, user_text: str = ""):
    sys = (system_persona or "").strip() + "\n\n" + SYSTEM_RULES
    msgs = [{"role": "system", "content": sys}]
    if history:
        # history deve ser no formato [{"role":"user"/"assistant","content":...}, ...]
        msgs.extend(history[-12:])
    if user_text:
        msgs.append({"role": "user", "content": user_text})
    return msgs

def _call(model: str, messages):
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.65,
        "max_tokens": 320,
    }
    r = requests.post(OPENROUTER_URL, headers=_headers(), data=json.dumps(body), timeout=45)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

def _post(txt: str) -> str:
    # remove emojis exóticos e espaços excessivos
    txt = re.sub(r"[\U00010000-\U0010ffff]", "", txt)
    txt = re.sub(r"\s{3,}", "  ", txt).strip()
    return txt

def generate_reply(user_text: str, system_persona: str, history=None) -> str:
    """
    Gera resposta textual usando OpenRouter, com fallback de modelos.
    Parâmetros:
      - user_text: texto do cliente
      - system_persona: instruções + persona (string)
      - history: lista de mensagens [{"role":"user"/"assistant","content":...}]
    Retorna:
      - string de resposta (pode ser curta), ou fallback padrão se falhar
    """
    if not OR_KEY:
        log.error("[AI] OPENROUTER_API_KEY ausente")
        return "Amor, me diz onde prefere (meu local no Villa Rosa, motel ou teu apê) e o horário — e como quer pagar (PIX, dinheiro ou cartão)."

    messages = _merge_messages(system_persona, history=history, user_text=user_text)

    last_err = None
    for m in MODEL_POOL:
        try:
            log.info("[AI] tentando modelo: %s", m)
            raw = _call(m, messages)
            return _post(raw)
        except Exception as e:
            last_err = e
            log.warning("[AI] falha com %s: %s", m, str(e)[:400])
            time.sleep(0.8)
    log.error("[AI] todos os modelos falharam: %s", last_err)
    return "Tá bom, amor. Me fala o local (meu local/motel/teu apê), o horário e a forma de pagamento (PIX, dinheiro ou cartão)."