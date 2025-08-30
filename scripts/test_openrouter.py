import requests
import json
import sys
from configs.config_loader import get_provider_config

def main():
    provider, settings = get_provider_config()
    if provider != "openrouter":
        print(f"⚠️ Config atual não está setada para 'openrouter', está como '{provider}'.")
        sys.exit(1)

    url = settings["endpoint"]
    api_key = settings["api_key"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "qwen2.5-7b-instruct",
        "messages": [
            {"role": "user", "content": "Diga apenas: Conexão OK ✅"}
        ]
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        print("✅ Requisição bem-sucedida!")
        print("Resposta da AI:", response.json()["choices"][0]["message"]["content"])
    else:
        print("❌ Erro na requisição:", response.status_code, response.text)

if __name__ == "__main__":
    main()