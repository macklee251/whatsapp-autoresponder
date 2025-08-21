# ai_provider.py
import os, time, logging, random, re
from typing import Optional, Dict, List
import requests

from persona import build_system_prompt, FEW_SHOTS

log = logging.getLogger("ai")

MODEL_POOL = [
    "mistralai/mistral-7b-instruct",
    "qwen/qwen2.5-7b-instruct",
    "google/gemma-2-9b-it",
]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DETAIL_HINT = re.compile(r"\b(detalhe|detalhes|explica|explicar|como funciona|me conta mais)\b", re.I)

def tidy(msg: str) -> str:
    m = (msg or "").strip()
    if len(m) > 700:  # permite mais do que antes, mas segura exageros
        m = m[:700].rsplit(".", 1)[0] + "."
    while "\n\n\n" in m:
        m = m.replace("\n\n\n", "\n\n")
    return m

def _call_openrouter(model: str, system_prompt: str, user_text: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY ausente")

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages += FEW_SHOTS
    messages.append({"role": "user", "content": user_text})

    wants_details = bool(DETAIL_HINT.search(user_text or ""))
    max_tokens = 360 if wants_details else 220

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.45,
        "top_p": 0.9,
        "frequency_penalty": 0.2,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://seu-dominio-ou-projeto",
        "X-Title": "whatsapp-autoresponder",
    }

    log.info("[OPENROUTER] model=%s", model)
    r = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=40)
    if r.status_code != 200:
        raise requests.HTTPError(f"status={r.status_code} body={r.text}")

    data = r.json()
    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content") or ""
    return tidy(content)

def generate_reply(user_text: str, profile: Optional[Dict] = None) -> str:
    system_prompt = build_system_prompt(profile)

    low = (user_text or "").lower()
    if any(k in low for k in ["menor", "menores", "underage", "14", "15", "16", "17"]):
        return "Não atendo menores de idade nem nada fora da lei. Se quiser, seguimos apenas dentro das regras."

    last_err = None
    for model in MODEL_POOL:
        try:
            log.info("[AI] usando modelo: %s", model)
            reply = _call_openrouter(model, system_prompt, user_text)
            if reply.strip():
                return reply
        except Exception as e:
            last_err = e
            log.warning("[AI] erro com %s: %s", model, getattr(e, "args", e))
            time.sleep(0.8 + random.random() * 0.7)

    if last_err:
        log.error("[AI] falha geral: %s", last_err)
    return "Entendi, amor. Me diz o local e o horário que prefira, e a forma de pagamento (pix, cartão ou dinheiro), que eu já confirmo tudo pra você."