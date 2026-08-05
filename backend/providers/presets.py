CLOUD_PRESETS: dict[str, dict] = {
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models_path": "/models",
        "has_pricing": True,
        "docs_url": "https://openrouter.ai/keys",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models_path": "/models",
        "has_pricing": False,
        "docs_url": "https://platform.openai.com/api-keys",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models_path": "/models",
        "has_pricing": False,
        "docs_url": "https://console.groq.com/keys",
    },
    "together": {
        "name": "Together",
        "base_url": "https://api.together.xyz/v1",
        "models_path": "/models",
        "has_pricing": False,
        "docs_url": "https://api.together.ai/settings/api-keys",
    },
}
