# ai_provider.py
import os, time, json, logging
from pathlib import Path
from typing import List, Dict, Optional

import requests
from dotenv import load_dotenv, find_dotenv

# Carrega .env do diretório atual
load_dotenv(find_dotenv(usecwd=True), override=True)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Pool de modelos (ordem de tentativa)
# Você pode editar via .env com AI_MODEL_POOL="model1,model2,..."
_pool_env = os.getenv("AI_MODEL_POOL", "").strip()
if _pool_env:
    MODEL_POOL = [m.strip() for m in _pool_env.split(",") if m.strip()]
else:
    MODEL_POOL = [
        "qwen/qwen2.5-7b-instruct",     # rápido/barato
        "mistralai/mistral-7b-instruct",
        "google/gemma-2-9b-it",
    ]

HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}" if OPENROUTER_API_KEY else "",
    "HTTP-Referer": os.getenv("OPENROUTER_REFERRER", "https://example.com"),
    "X-Title": os.getenv("OPENROUTER_TITLE", "whatsapp-autoresponder"),
    "Content-Type": "application/json",
}

log = logging.getLogger("ai")
logging.basicConfig(level=logging.INFO)

class AIError(Exception):
    pass

def _chat(model: str, messages: List[Dict], temperature: float = 0.6, max_tokens: int = 300) -> str:
    if not OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY ausente no .env")

    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    log.info("[OPENROUTER] model=%s", model)
    r = requests.post(url, headers=HEADERS, json=payload, timeout=60)
    if r.status_code != 200:
        raise AIError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise AIError(f"Resposta inesperada: {json.dumps(data)[:300]}")

def generate_reply_with_fallback(
    user_text: str,
    persona: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    temperature: float = 0.6,
    max_tokens: int = 300,
) -> str:
    """
    Tenta vários modelos do OpenRouter até obter uma resposta.
    """
    system_prompt = persona or os.getenv("DEFAULT_PERSONA", "").strip()
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    last_err = None
    for model in MODEL_POOL:
        try:
            log.info("[AI] usando modelo: %s", model)
            reply = _chat(model, messages, temperature=temperature, max_tokens=max_tokens)
            return reply
        except Exception as e:
            last_err = e
            log.warning("[AI] falha com %s: %s", model, e)
            # aguarda um pouco entre tentativas (backoff leve)
            time.sleep(1.5)

    # fallback final – algo curto para não travar o fluxo
    log.error("[AI] todas as tentativas falharam: %s", last_err)
    return "Certo, amor. Pode me dizer o bairro e o horário que prefere? 💕"

# Compatibilidade: quem importar generate_reply continua funcionando
def generate_reply(*args, **kwargs) -> str:
    return generate_reply_with_fallback(*args, **kwargs)