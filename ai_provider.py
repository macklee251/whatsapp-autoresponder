# ai_provider.py — pool de modelos com fallback, retry e cooldown
import os
import time
import json
import logging
import requests
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai")

AI_PROVIDER   = os.getenv("AI_PROVIDER", "openrouter").lower().strip()
OPENROUTER_KEY= os.getenv("OPENROUTER_API_KEY", "").strip()
OLLAMA_BASE   = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434").strip()

# Pool de modelos (ordem = prioridade). Ex: "qwen/... , mistral/... , google/gemma-2-9b-it"
_pool_raw = os.getenv("AI_MODEL_POOL", "").strip()
if not _pool_raw:
    # fallback para manter compat com variáveis antigas
    one = os.getenv("AI_MODEL", "qwen/qwen2.5-7b-instruct").strip()
    MODEL_POOL: List[str] = [one]
else:
    MODEL_POOL = [m.strip() for m in _pool_raw.split(",") if m.strip()]

# Parâmetros de robustez
MAX_ATTEMPTS_PER_MODEL = 3          # tentativas por modelo
BACKOFF_BASE_SEC       = 1.5        # backoff exponencial: 1.5, 3, 6, 9...
MODEL_COOLDOWN_SEC     = 300        # 5 minutos em cooldown após circuit breaker
FAIL_THRESHOLD         = 3          # N falhas seguidas para abrir o "disjuntor"

# Estado em memória (falhas e cooldown por modelo)
_model_state: Dict[str, Dict] = {
    m: {"fails": 0, "cooldown_until": 0.0} for m in MODEL_POOL
}

BASE_SYSTEM = (
    "Você é um assistente de atendimento comercial adulto, educado e persuasivo. "
    "Jamais envolva menores de idade, violência, ilegalidades ou coerção. "
    "Não conceda descontos; conduza a conversa para confirmar local, data/horário e forma de pagamento. "
    "Se o cliente enviar áudio/vídeo/imagem, peça gentilmente para enviar em texto."
)

def _messages(persona: str, user_text: str):
    sysmsg = BASE_SYSTEM + (f" Persona: {persona.strip()}." if persona else "")
    return [
        {"role": "system", "content": sysmsg},
        {"role": "user",   "content": user_text},
    ]

# -------------------- Provedores --------------------

def _openrouter_chat(model: str, messages, temperature=0.7, max_tokens=240, timeout=12):
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente no .env")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://example.com",
        "X-Title": "WA Autoresponder",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    log.info(f"[OPENROUTER] model={model}")
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    # Deixa 429/503 serem tratados acima (retry/fallback)
    if r.status_code in (429, 503):
        raise requests.HTTPError(f"{r.status_code} upstream", response=r)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

def _ollama_chat(model: str, messages, temperature=0.7, max_tokens=240, timeout=30):
    url = f"{OLLAMA_BASE}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    log.info(f"[OLLAMA] model={model} base={OLLAMA_BASE}")
    r = requests.post(url, json=payload, timeout=timeout)
    if r.status_code in (429, 503):
        raise requests.HTTPError(f"{r.status_code} upstream", response=r)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message") or {}
    return (msg.get("content") or "").strip()

def _call_provider(model: str, messages):
    if AI_PROVIDER == "openrouter":
        return _openrouter_chat(model, messages)
    elif AI_PROVIDER == "ollama":
        return _ollama_chat(model, messages)
    else:
        raise RuntimeError(f"AI_PROVIDER inválido: {AI_PROVIDER}")

# -------------------- Orquestração (retry + pool + cooldown) --------------------

def _now() -> float:
    return time.time()

def _available_models() -> List[str]:
    ts = _now()
    return [m for m in MODEL_POOL if _model_state.get(m, {}).get("cooldown_until", 0) <= ts]

def _mark_success(model: str):
    st = _model_state.setdefault(model, {"fails": 0, "cooldown_until": 0.0})
    st["fails"] = 0
    st["cooldown_until"] = 0.0

def _mark_failure(model: str, last_code: int | None = None):
    st = _model_state.setdefault(model, {"fails": 0, "cooldown_until": 0.0})
    st["fails"] += 1
    # Se muitas falhas em sequência, abre cooldown
    if st["fails"] >= FAIL_THRESHOLD or (last_code in (429, 503) and st["fails"] >= 2):
        st["cooldown_until"] = _now() + MODEL_COOLDOWN_SEC
        log.warning(f"[AI] cooldown ativado para {model} por {MODEL_COOLDOWN_SEC}s (fails={st['fails']})")

def ai_reply(user_text: str, persona: str = "") -> str:
    """
    Escolhe um modelo do pool, tenta até MAX_ATTEMPTS_PER_MODEL com backoff,
    se falhar aplica cooldown e passa pro próximo. Lança exceção se todos falharem.
    """
    messages = _messages(persona, user_text)
    tried = []

    for _round in range(len(MODEL_POOL)):  # no máx. passa uma vez por todos
        candidates = _available_models()
        if not candidates:
            # todos em cooldown: espera o menor cooldown e tenta de novo
            next_ready = min(_model_state[m]["cooldown_until"] for m in MODEL_POOL)
            sleep_for = max(0.5, next_ready - _now())
            log.info(f"[AI] todos os modelos em cooldown; aguardando {sleep_for:.1f}s")
            time.sleep(sleep_for)
            candidates = _available_models()

        # round‑robin simples: pega o primeiro disponível que ainda não tentamos neste ciclo
        model = next((m for m in candidates if m not in tried), None)
        if not model:
            # se todos candidatos já foram tentados neste ciclo, reseta a lista e pega o primeiro disponível
            tried = []
            model = candidates[0]

        tried.append(model)
        log.info(f"[AI] usando modelo: {model}")

        # tentativas com backoff
        delay = BACKOFF_BASE_SEC
        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                text = _call_provider(model, messages)
                if not text or not text.strip():
                    raise RuntimeError("Resposta vazia")
                _mark_success(model)
                return text.strip()
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", None)
                body = ""
                try:
                    body = e.response.text
                except Exception:
                    pass
                log.warning(f"[AI] HTTPError model={model} code={code} attempt={attempt} body={body[:180]}")
                _mark_failure(model, last_code=code)
                if attempt < MAX_ATTEMPTS_PER_MODEL and code in (429, 503):
                    time.sleep(delay)
                    delay = min(delay * 2, 10.0)  # 1.5, 3, 6, 10
                    continue
                break  # troca de modelo
            except Exception as e:
                log.warning(f"[AI] erro model={model} attempt={attempt}: {e}")
                _mark_failure(model)
                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    time.sleep(delay)
                    delay = min(delay * 2, 10.0)
                    continue
                break  # troca de modelo

    # se chegou aqui, todo o pool falhou
    raise RuntimeError("Todos os modelos do pool falharam (rate-limit/erro).")