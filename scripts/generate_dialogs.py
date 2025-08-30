import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests

# Caminho do projeto
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Configurações
OVERWRITE = "sim"  # "sim" para substituir, "nao" para adicionar
NUM_DIALOGOS = 4  # Gerar 4 diálogos
PERSONALITIES_FILE = PROJECT_ROOT / "data" / "personas" / "personas_gp_client.json"
OUTPUT_FILE = PROJECT_ROOT / "data" / "dialogs" / "generated_dialogs.jsonl"

def ensure_dirs():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_personalities() -> Dict[str, List[Dict[str, Any]]]:
    with open(PERSONALITIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "garotas" not in data or "clientes" not in data:
        raise ValueError("Arquivo de personas precisa conter chaves 'garotas' e 'clientes'.")
    if not isinstance(data["garotas"], list) or not isinstance(data["clientes"], list):
        raise ValueError("'garotas' e 'clientes' devem ser listas.")
    if len(data["garotas"]) == 0 or len(data["clientes"]) == 0:
        raise ValueError("É necessário pelo menos 1 garota e 1 cliente.")
    return data

def resolve_model_name(default_model: str) -> str:
    if "/" in default_model:
        return default_model
    mapping = {
        "qwen2.5-7b-instruct": "qwen/qwen-2.5-7b-instruct",
        "llama3.1-70b-instruct": "meta-llama/llama-3.1-70b-instruct"
    }
    return mapping.get(default_model.strip().lower(), default_model)

def load_config() -> Dict[str, Any]:
    with open(PROJECT_ROOT / "configs" / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def build_api_payload(garota: Dict[str, Any], cliente: Dict[str, Any], model_name: str, meta: str) -> Dict[str, Any]:
    system_prompt = (
        f"Você é uma assistente que GERA diálogos em português do Brasil entre uma acompanhante (mulher adulta) "
        f"e um cliente (homem adulto). O diálogo deve ser REALISTA, com tom definido pela descrição da garota: {garota['personalidade']['descricao']}. "
        f"Use papéis 'human' para cliente e 'assistant' para garota. "
        f"Use gírias como 'delícia', 'safado', 'se acabar' pra garotas safadas, ou tom sofisticado pra garotas elegantes. "
        f"Evite repetir informações (ex: preço, limites) a menos que necessário. Respeite os limites estritamente, negando pedidos fora deles com charme. "
        f"Se o cliente mencionar algo explícito, use linguagem quente (pra garotas safadas) ou sensual (pra elegantes) após confirmar consentimento. "
        f"Inclua referências a São Paulo (ex: motel Love Story, bairro {garota['localizacao']['bairro']}) quando fizer sentido. "
        f"Evite frases sem sentido ou fora do contexto cultural. "
        f"Meta do diálogo: {meta}. "
        f"FORMATO DE SAÍDA (JSON estrito):\n"
        f"{{\n"
        f'  "messages": [\n'
        f'    {{"role": "human", "content": "..."}},\n'
        f'    {{"role": "assistant", "content": "..."}},\n'
        f"    ... (8 a 12 turnos, começando com human e alternando)\n"
        f"  ]\n"
        f"}}\n"
        f"Responda SOMENTE com o JSON, sem texto extra."
    )
    garota_desc = json.dumps(garota, ensure_ascii=False)
    cliente_desc = json.dumps(cliente, ensure_ascii=False)
    user_prompt = "Gere diálogo com meta: " + meta + ". Garota: " + garota_desc + ". Cliente: " + cliente_desc
    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.8,
        "max_tokens": 900
    }

def call_chat_api(payload: Dict[str, Any]) -> str:
    config = load_config()
    api_key = config["settings"]["api_key"]
    endpoint = config["settings"]["endpoint"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/macklee251/whatsapp-autoresponder",
        "X-Title": "whatsapp-autoresponder-dataset-gen"
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def best_effort_json_parse(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            return None
    return None

def validate_messages(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(obj, dict):
        return None
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 6:
        return None
    roles = [m.get("role") for m in msgs]
    contents_ok = all(isinstance(m.get("content"), str) and m["content"].strip() for m in msgs)
    if not contents_ok:
        return None
    if roles[0] != "human":
        return None
    for i in range(1, len(roles)):
        if roles[i] == roles[i-1] or roles[i] not in ("human", "assistant"):
            return None
    return {"messages": [{"role": m["role"], "content": m["content"].strip()} for m in msgs]}

def fallback_dialog(garota: Dict[str, Any], cliente: Dict[str, Any]) -> Dict[str, Any]:
    u = f"Oi {garota.get('nome')}, tudo bem? Quanto é pra hoje?"
    a = f"Oi {cliente.get('nome')}! Trabalho de forma discreta. A base é {garota.get('preco_base')}. Quais horários te atendem?"
    u2 = "Penso hoje à noite. Você atende em hotel? Qual região fica melhor?"
    a2 = f"Atendo em hotéis selecionados. Em {garota.get('cidade')}, posso na região que preferir. Confirmo com antecedência."
    u3 = "Perfeito. Forma de pagamento e alguma regra importante?"
    a3 = "Pix no local. Regras: respeito, sempre com proteção, sem gravação. Sem conteúdo explícito por mensagem."
    u4 = "Combinado. Te aviso ao chegar. Obrigado pela clareza."
    a4 = f"Eu que agradeço, {cliente.get('nome')}. Até mais!"
    return {
        "messages": [
            {"role": "human", "content": u},
            {"role": "assistant", "content": a},
            {"role": "human", "content": u2},
            {"role": "assistant", "content": a2},
            {"role": "human", "content": u3},
            {"role": "assistant", "content": a3},
            {"role": "human", "content": u4},
            {"role": "assistant", "content": a4},
        ]
    }

def pair_indices(num: int, n_g: int, n_c: int) -> List[tuple]:
    pairs = []
    g = 0
    for i in range(num):
        c = i % n_c
        if i > 0 and c == 0:
            g = (g + 1) % n_g
        pairs.append((g, c))
    return pairs

def generate_dialogs():
    ensure_dirs()
    personas = load_personalities()
    garotas = personas["garotas"]
    clientes = personas["clientes"]
    config = load_config()
    model_name = resolve_model_name(config["settings"]["default_model"])
    metas = [
        "conversa natural com putaria e marcação no final",
        "desentendimento leve sem xingamentos e sem marcação",
        "cliente arrogante com resposta arrogante da garota",
        "cliente pede algo fora dos limites e garota nega"
    ]
    pairs = pair_indices(NUM_DIALOGOS, len(garotas), len(clientes))
    print(f"Geração iniciada | modelo: {model_name} | diálogos: {len(pairs)}")
    ok, fail = 0, 0
    mode = "w" if OVERWRITE.lower() == "sim" else "a"
    with open(OUTPUT_FILE, mode, encoding="utf-8") as out:
        for idx, (gi, ci) in enumerate(pairs, start=1):
            garota = garotas[gi]
            cliente = clientes[ci]
            meta = metas[idx - 1]
            payload = build_api_payload(garota, cliente, model_name, meta)
            try:
                raw = call_chat_api(payload)
                obj = best_effort_json_parse(raw)
                obj = validate_messages(obj) if obj else None
                if not obj:
                    raise ValueError("JSON inválido do modelo, usando fallback.")
                output = {
                    "dialog": obj,
                    "garota": garota,
                    "cliente": cliente,
                    "meta": meta
                }
                out.write(json.dumps(output, ensure_ascii=False) + "\n")
                print(f"[{idx}] salvo (garota={garota['nome']} x cliente={cliente['nome']}, meta={meta})")
                ok += 1
            except Exception as e:
                print(f"[{idx}] Falha ({e}); usando fallback.")
                obj = fallback_dialog(garota, cliente)
                output = {
                    "dialog": obj,
                    "garota": garota,
                    "cliente": cliente,
                    "meta": meta
                }
                out.write(json.dumps(output, ensure_ascii=False) + "\n")
                print(f"[{idx}] salvo (garota={garota['nome']} x cliente={cliente['nome']}, meta={meta})")
                fail += 1
    print(f"Concluído. Sucesso: {ok} | Fallbacks: {fail} | arquivo: {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_dialogs()