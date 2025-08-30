import requests
from configs.config_loader import get_provider_config


def main():
    # Carregar provider e settings do config.json
    provider, settings = get_provider_config()
    print(f"Usando provider: {provider}")

    # Pegar chave de API e endpoint do provider
    api_key = settings.get("api_key")
    endpoint = settings.get("endpoint")
    model = settings.get("model", "gpt-3.5-turbo")

    if not api_key or not endpoint:
        raise ValueError("Configuração inválida: api_key ou endpoint ausentes")

    # Cabeçalhos da requisição
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Payload para testar a requisição
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Você é uma IA de teste."},
            {"role": "user", "content": "Olá, pode confirmar que está funcionando?"},
        ],
    }

    # Enviar requisição
    response = requests.post(endpoint, headers=headers, json=payload)

    # Mostrar saída bruta (debug)
    print("Status code:", response.status_code)
    print("Resposta bruta:", response.text)

    try:
        data = response.json()
        print("Resposta da AI:", data["choices"][0]["message"]["content"])
    except Exception as e:
        print("Erro ao processar resposta:", e)


if __name__ == "__main__":
    main()