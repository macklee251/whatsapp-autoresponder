# /workspaces/whatsapp-autoresponder/configs/config_loader.py
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Dict, Tuple, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "config.json"

class ConfigError(RuntimeError):
    pass

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config não encontrada em: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"JSON inválido em {path}: {e}")

def load_config() -> Dict[str, Any]:
    return _read_json(CONFIG_PATH)

def get_provider_config(provider: str | None = None) -> Tuple[str, Dict[str, Any]]:
    """
    Retorna (provider_name, settings_dict).
    Prioridade para escolha do provider:
      1) parâmetro da função (se informado)
      2) variável de ambiente PROVIDER
      3) campo "provider" do config.json
    """
    cfg = load_config()

    # estrutura esperada
    if "providers" not in cfg or not isinstance(cfg["providers"], dict):
        raise ConfigError('Config deve conter {"providers": {...}}')

    # quem é o provider ativo?
    active = (
        provider
        or os.getenv("PROVIDER")
        or cfg.get("provider")
    )
    if not active:
        raise ConfigError(
            'Provider não definido. Informe "provider" no config.json, '
            "ou set a env PROVIDER, ou passe parâmetro para get_provider_config()."
        )

    providers = cfg["providers"]
    if active not in providers:
        existentes = ", ".join(providers.keys()) or "(nenhum)"
        raise ConfigError(f'Provider "{active}" não existe em config. Disponíveis: {existentes}')

    settings = providers[active]
    if not isinstance(settings, dict):
        raise ConfigError(f'Bloco do provider "{active}" deve ser um objeto JSON.')

    # validações leves por provider
    if active == "openrouter":
        for k in ["api_key", "base_url"]:
            if not settings.get(k):
                raise ConfigError(f'openrouter: campo obrigatório ausente: "{k}"')
        # header padrão (sem expor chave)
        settings.setdefault("headers", {
            "Authorization": f"Bearer {settings['api_key']}",
            "HTTP-Referer": settings.get("referer", "https://example.com"),
            "X-Title": settings.get("app_title", "whatsapp-autoresponder"),
            "Content-Type": "application/json",
        })
        # default endpoint de chat
        settings.setdefault("chat_path", "/v1/chat/completions")

    elif active == "google_cloud":
        for k in ["project_id", "location", "model", "api_key"]:
            if not settings.get(k):
                raise ConfigError(f'google_cloud: campo obrigatório ausente: "{k}"')

    elif active == "runpod":
        for k in ["api_key", "base_url"]:
            if not settings.get(k):
                raise ConfigError(f'runpod: campo obrigatório ausente: "{k}"')

    # Permitir overrides via env (ex.: OPENROUTER_API_KEY)
    env_overrides = {
        "openrouter": ("OPENROUTER_API_KEY", "api_key"),
        "runpod": ("RUNPOD_API_KEY", "api_key"),
        "google_cloud": ("GOOGLE_API_KEY", "api_key"),
    }
    if active in env_overrides:
        env_name, field = env_overrides[active]
        if os.getenv(env_name):
            settings[field] = os.getenv(env_name)

    return active, settings