"""Providers this build has been run against, and what was observed.

Shared by the local server and the Worker so the settings panel offers the
same list wherever it is served from. Any OpenAI-compatible endpoint works;
these are starting points with notes, not a whitelist.
"""

PROVIDERS = {
    "sarvam": {
        "label": "Sarvam AI",
        "base_url": "https://api.sarvam.ai/v1",
        "models": ["sarvam-105b", "sarvam-105b-conversations"],
        "note": "Free tier. Tool calling works; answers in about 6 seconds.",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.8-flash",
                   "gemini-2.5-flash"],
        "note": "Free tier. 3.7-flash is the fastest tested; 3.8-flash hits the "
                "free request quota quickly.",
    },
    "cloudflare": {
        "label": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/"
                    "ACCOUNT_ID/ai/v1",
        "models": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "@cf/mistralai/mistral-small-3.1-24b-instruct",
                   "@cf/qwen/qwen3-30b-a3b-fp8"],
        "note": "Put your account id in the URL. Avoid @cf/openai/gpt-oss-*: "
                "they answer into a reasoning field and return no content.",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": ["meta/llama-3.3-70b-instruct", "qwen/qwen3-next-80b-a3b-instruct"],
        "note": "Free developer tier. Untested here -- bring a key and try it.",
    },
    "openai": {
        "label": "OpenAI-compatible (other)",
        "base_url": "https://api.openai.com/v1",
        "models": [],
        "note": "Any endpoint that serves /chat/completions with tool calling.",
    },
}

