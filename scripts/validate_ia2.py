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

# Histórico de intents
detected_intents = {"local": "", "data": "", "pagamento": "", "fora_do_perfil": ""}

# Prompt pra análise de intents
system_prompt = (
    f"Você é uma IA que analisa mensagens de um cliente para uma acompanhante chamada {garota['nome']}. "
    f"Limites da garota: {', '.join(garota['personalidade']['tabus'])}. "
    f"Locais de atendimento: {', '.join(garota['localizacao']['locais_atendimento'])}. "
    f"Detecte nas mensagens: "
    f"- Local (ex.: 'motel X', 'Pinheiros'). "
    f"- Data (ex.: 'amanhã às 20h', 'hoje à noite'). "
    f"- Forma de pagamento (ex.: 'Pix', 'dinheiro'). "
    f"- Pedidos fora do perfil (ex.: algo nos tabus ou não listado em locais/serviços). "
    f"Responda com um JSON estrito contendo: "
    f"{{\"intents\": {{\"local\": \"\", \"data\": \"\", \"pagamento\": \"\", \"fora_do_perfil\": \"\"}}}}. "
    f"Se nada for detectado, deixe os campos vazios. Responda só o JSON."
)

# Função pra analisar intents
def analyze_intents(client_message):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": client_message}
        ],
        "temperature": 0.7,
        "max_tokens": 150
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/your-repo/whatsapp-autoresponder",
        "X-Title": "whatsapp-autoresponder-intent-analysis"
    }
    try:
        response = requests.post(ENDPOINT, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])
    except requests.exceptions.HTTPError as e:
        print(f"Erro HTTP: {e.response.text}")
        print(f"Payload enviado: {json.dumps(payload, indent=2)}")
        raise
    except json.JSONDecodeError:
        print("Erro: Resposta da API não é um JSON válido.")
        raise

# Função pra simular envio de e-mail
def simulate_email(garota, intents):
    telefone = "123-456-7890"  # Placeholder, substitua por número real se disponível
    if intents["intents"]["local"] and intents["intents"]["data"] and intents["intents"]["pagamento"]:
        mensagem = (
            f"O cliente de número de telefone {telefone} quer marcar com você no {intents['intents']['local']}, "
            f"{intents['intents']['data']}, e o pagamento será {intents['intents']['pagamento']}. "
            f"Por favor, entre em contato e confirme o agendamento."
        )
        print(f"Enviando e-mail para {garota['nome']} ({garota['localizacao']['cidade']}, {garota['localizacao']['bairro']}):")
        print(f"Assunto: Confirmação de agendamento")
        print(f"Corpo: {mensagem}")
    elif intents["intents"]["fora_do_perfil"]:
        mensagem = (
            f"O cliente de número de telefone {telefone} está interessado em {intents['intents']['fora_do_perfil']}. "
            f"Por favor, entre em contato e finalize o atendimento."
        )
        print(f"Enviando e-mail para {garota['nome']} ({garota['localizacao']['cidade']}, {garota['localizacao']['bairro']}):")
        print(f"Assunto: Solicitação fora do perfil")
        print(f"Corpo: {mensagem}")

# Loop interativo
print(f"Analisando intents para {garota['nome']}. Digite 'sair' pra encerrar.")
while True:
    client_message = input("Você (cliente): ")
    if client_message.lower() == "sair":
        break
    try:
        intents = analyze_intents(client_message)
        detected_intents.update(intents["intents"])
        print(f"Intents detectados: {json.dumps(detected_intents, ensure_ascii=False)}")
        simulate_email(garota, {"intents": detected_intents})
    except Exception as e:
        print(f"Erro: {str(e)}")