import requests
import json
from pathlib import Path

# Caminho do projeto
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Carregar personas de personas_gp.json
def load_personas():
    personas_file = PROJECT_ROOT / "data" / "personas" / "personas_gp.json"
    with open(personas_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["garotas"]

# Carregar config de config.json
def load_config():
    config_file = PROJECT_ROOT / "configs" / "config.json"
    with open(config_file, "r", encoding="utf-8") as f:
        return json.load(f)

# Escolher garota (1 a 10)
garota_num = int(input("Escolha o número da garota (1 a 10): ")) - 1
if not 0 <= garota_num <= 9:
    raise ValueError("Número da garota deve ser de 1 a 10.")

# Carregar personas e selecionar garota
personas = load_personas()
garota = personas[garota_num]

# Carregar config
config = load_config()
API_KEY = config["settings"]["api_key"]
ENDPOINT = config["settings"]["endpoint"]
MODEL = "openai/gpt-4o"

# Histórico da conversa
conversation_history = []

# Prompt com perfil da garota
system_prompt = (
    f"Você é {garota['nome']}, {garota['idade']} anos, de {garota['localizacao']['cidade']}, {garota['localizacao']['bairro']}. "
    f"Descrição: {garota['personalidade']['descricao']}. "
    f"Limites: {', '.join(garota['personalidade']['tabus'])}. "
    f"Preço: {garota['profissional']['preco_base']['valor']} BRL/h. "
    f"Gostos: {', '.join(garota['personalidade']['gostos'])}. "
    f"Fetiches: {', '.join(garota['personalidade']['fetiches'])}. "
    f"Características físicas: {', '.join(garota['aparencia']['corpo']['caracteristicas'])}. "
    f"Locais de atendimento: {', '.join(garota['localizacao']['locais_atendimento'])}. "
    "Responda como ela, em português do Brasil, de forma realista e fiel ao perfil, usando tom definido pela descrição. "
    "Seja breve, direto e use gírias ou tom sofisticado conforme a personalidade. "
    "Respeite os limites estritamente, negando pedidos fora deles com charme. "
    "Se explícito, use linguagem quente (pra garotas safadas) ou sensual (pra elegantes) após consentimento. "
    "Inclua referências a São Paulo (ex: bairro, motel) se fizer sentido. "
    "Responda só a mensagem, sem emojis ou texto extra."
)

# Função pra gerar resposta com debug
def get_response(client_message):
    messages = [
        {"role": "system", "content": system_prompt}
    ] + conversation_history[-5:] + [
        {"role": "user", "content": client_message}
    ]
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 200
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/your-repo/whatsapp-autoresponder",
        "X-Title": "whatsapp-autoresponder"
    }
    try:
        response = requests.post(ENDPOINT, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        print(f"Erro HTTP: {e.response.text}")
        print(f"Payload enviado: {json.dumps(payload, indent=2)}")
        raise

# Loop interativo
print(f"Simulando conversa com {garota['nome']}. Digite 'sair' pra encerrar.")
while True:
    client_message = input("Você (cliente): ")
    if client_message.lower() == "sair":
        break
    try:
        response = get_response(client_message)
        print(f"{garota['nome']}: {response}")
        conversation_history.append({"role": "user", "content": client_message})
        conversation_history.append({"role": "assistant", "content": response})
    except Exception as e:
        print(f"Erro: {str(e)}")