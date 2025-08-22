# ai_provider.py
import os, time, random, logging
import requests

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.getenv("OPENROUTER_API_KEY")

# Ordem de preferência (pode reordenar/ajustar)
MODEL_POOL = [
    "nousresearch/nous-hermes-2-mixtral-8x7b-dpo",
    "qwen/qwen2.5-32b-instruct",
    "mistralai/mixtral-8x22b-instruct",
    "austism/airoboros-l2-70b",  # se esse ID não existir mais, será ignorado no fallback
]

# “Sistema” fixo para manter o papel da personagem.
BASE_SYSTEM = (
    "Você é **Gabriele**, 20 anos, ninfeta magrinha (1,60), loira, seios pequenos; "
    "atende no próprio apto no Villa Rosa, em motéis ou no apê do cliente (se for apartamento). "
    "Valores: R$300 no seu local; R$500 em motel/apê do cliente. Aceita PIX, cartão e dinheiro. "
    "Estilo: namoradinha romântica, flerte leve, direta, persuasiva; pode falar de sexo de forma aberta "
    "e adulta (sem menores e sem ilegalidades). Jamais negocia desconto. "
    "Se o cliente enviar áudio/foto/vídeo, diga educadamente que só consegue ler mensagens escritas. "
    "Responda **curto a médio** (2–10 frases) e com português natural, podendo ter pequenos desvios; "
    "evite exagero de emojis. Se o cliente pedir muitos detalhes do atendimento, pode estender um pouco. "
    "Seu objetivo é conduzir para fechar: local + horário/data + forma de pagamento. "
    "Após detectar que **fechou**, responda algo simples e pare (o app cuidará do restante)."
)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}" if API_KEY else "",
    "HTTP-Referer": "https://openrouter.ai",  # opcional, mas recomendado
    "X-Title": "WA Autoresponder",
}

TIMEOUT_S = 30
MAX_TOKENS = 280   # para não soltar textão sempre
TEMPERATURE = 0.8

log = logging.getLogger("ai")

class AIError(Exception):
    pass

def _one_call(model: str, history):
    """Faz 1 chamada a um modelo do OpenRouter e retorna o texto."""
    if not API_KEY:
        raise AIError("OPENROUTER_API_KEY ausente no ambiente")

    payload = {
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "messages": history,
    }

    log.info("[OPENROUTER] model=%s", model)
    r = requests.post(OPENROUTER_BASE, headers=HEADERS, json=payload, timeout=TIMEOUT_S)
    if r.status_code != 200:
        # devolve o corpo para debug
        raise AIError(f"HTTP {r.status_code}: {r.text}")

    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise AIError(f"Resposta inesperada: {data}") from e

def generate_reply_with_fallback(chat_turns):
    """
    Recebe chat_turns = [{"role":"user"/"assistant","content":"..."}...]
    Retorna string com a resposta. Tenta modelos em fallback.
    """
    # injeta o system no topo
    messages = [{"role":"system","content": BASE_SYSTEM}] + chat_turns

    last_err = None
    for idx, model in enumerate(MODEL_POOL, start=1):
        log.info("[AI] usando modelo: %s", model)
        try:
            return _one_call(model, messages)
        except Exception as e:
            last_err = e
            log.warning("[AI] falha com %s (%d/%d): %s", model, idx, len(MODEL_POOL), str(e)[:200])
            # backoff rápido
            time.sleep(1.5 + random.random()*1.5)
            continue

    # se todos falharam
    raise AIError(f"Todos modelos falharam. Último erro: {last_err}")