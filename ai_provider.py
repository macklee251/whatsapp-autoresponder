# ai_provider.py
import os, time, random, logging, requests

log = logging.getLogger("ai")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

MODEL_POOL = [
    "nousresearch/nous-hermes-2-mixtral-8x7b-dpo",
    "qwen/qwen2.5-32b-instruct",
    "mistralai/mixtral-8x22b-instruct",
    "austism/airoboros-l2-70b",
]

HEADERS = {
    "Authorization": f"Bearer {API_KEY}" if API_KEY else "",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://openrouter.ai",
    "X-Title": "WA Autoresponder",
}

TEMPERATURE = 0.8
MAX_TOKENS  = 280
TIMEOUT_S   = 35


def _one_call(model: str, messages: list[dict]) -> str:
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente no ambiente")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    log.info("[OPENROUTER] model=%s", model)
    r = requests.post(OPENROUTER_URL, headers=HEADERS, json=payload, timeout=TIMEOUT_S)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError(f"Resposta inesperada: {data}")


def generate_reply(history, system_prompt=None, model_hint=None) -> str:
    """
    Função principal chamada pelo app.py.
    - history: lista de mensagens (role+content)
    - system_prompt/model_hint: mantidos por compatibilidade
    Retorna a resposta do modelo, ou fallback textual.
    """
    last_err = None
    for idx, model in enumerate(MODEL_POOL, start=1):
        try:
            return _one_call(model, history)
        except Exception as e:
            last_err = e
            log.warning("[AI] falha com %s (%d/%d): %s",
                        model, idx, len(MODEL_POOL), str(e)[:200])
            time.sleep(1.0 + random.random()*1.0)
    log.error("[AI] todos os modelos falharam: %s", last_err)
    return ("Amor, me diz só: prefere no meu local (Villa Rosa, R$300), "
            "em motel ou no seu apê (R$500)? E qual horário e pagamento (PIX/cartão/dinheiro)?")