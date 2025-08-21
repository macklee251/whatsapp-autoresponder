# ai_provider.py
import os, requests

AI_PROVIDER      = os.getenv("AI_PROVIDER", "openrouter").lower()
AI_MODEL         = os.getenv("AI_MODEL", "google/gemma-2-9b-it")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY")
OLLAMA_BASE      = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")

BASE_SYSTEM = (
    "Você é um assistente de atendimento comercial para adultos, educado e persuasivo. "
    "NUNCA envolva menores, nada ilegal, nada coercitivo. "
    "Não ofereça descontos. Conduza para fechar local, data e forma de pagamento. "
    "Se o cliente enviar áudio, vídeo ou imagem, peça delicadamente para enviar em texto."
)

def _messages(persona: str, user_text: str):
    system = BASE_SYSTEM
    if persona:
        system += f" Persona: {persona.strip()}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

def _call_openrouter(messages):
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente no .env")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 280,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=45)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

def _call_ollama(messages):
    url = f"{OLLAMA_BASE}/api/chat"
    payload = {"model": AI_MODEL, "messages": messages, "stream": False, "options": {"temperature": 0.7, "num_predict": 280}}
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return (data.get("message") or {}).get("content", "").strip()

def ai_reply(user_text: str, persona: str = "") -> str:
    msgs = _messages(persona, user_text)
    if AI_PROVIDER == "openrouter":
        return _call_openrouter(msgs)
    elif AI_PROVIDER == "ollama":
        return _call_ollama(msgs)
    else:
        raise RuntimeError(f"AI_PROVIDER inválido: {AI_PROVIDER}")