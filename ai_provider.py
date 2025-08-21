# ai_provider.py
import os
import requests
import logging

# Logs básicos (apenas INFO pra não poluir)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai")

AI_PROVIDER     = os.getenv("AI_PROVIDER", "openrouter").lower()
AI_MODEL        = os.getenv("AI_MODEL", "google/gemma-2-9b-it")
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY")
OLLAMA_BASE     = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")

# Regras base de conduta
BASE_SYSTEM = (
    "Você é um assistente de atendimento comercial adulto, educado e persuasivo. "
    "Jamais envolva menores de idade, violência, ilegalidades ou coerção. "
    "Não conceda descontos; conduza a conversa para confirmar local, data/horário e forma de pagamento. "
    "Se o cliente enviar áudio/vídeo/imagem, peça gentilmente para enviar em texto."
)

def _messages(persona: str, user_text: str):
    system = BASE_SYSTEM
    if persona:
        system += f" Persona: {persona.strip()}."
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

def _call_openrouter(messages, temperature=0.7, max_tokens=240):
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente no .env")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        # opcionais, ajudam na identificação do app no painel do OpenRouter
        "HTTP-Referer": "https://example.com",
        "X-Title": "WA Autoresponder",
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    log.info(f"[OPENROUTER] model={AI_MODEL}")
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    return text

def _call_ollama(messages, temperature=0.7, max_tokens=240):
    url = f"{OLLAMA_BASE}/api/chat"
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    log.info(f"[OLLAMA] model={AI_MODEL} base={OLLAMA_BASE}")
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message") or {}
    return (msg.get("content") or "").strip()

def ai_reply(user_text: str, persona: str = "") -> str:
    """
    Retorna resposta do provedor configurado.
    Lança exceção se falhar (app.py faz fallback).
    """
    msgs = _messages(persona, user_text)
    try:
        if AI_PROVIDER == "openrouter":
            return _call_openrouter(msgs)
        elif AI_PROVIDER == "ollama":
            return _call_ollama(msgs)
        else:
            raise RuntimeError(f"AI_PROVIDER inválido: {AI_PROVIDER}")
    except Exception as e:
        log.exception("AI call failed")
        raise