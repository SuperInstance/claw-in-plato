"""
port/models — Call external AI models for generation.
Actions: generate (text or image), list (available models)
Configurable via env vars: SILICONFLOW_API_KEY, DEEPSEEK_API_KEY, etc.
"""
import json, urllib.request, os
from ports import Port

class ModelsPort(Port):
    name = "models"
    description = "Generate text, images, and media via external AI models"
    room = "port/models"
    
    def __init__(self):
        super().__init__()
        # Auto-configure from env
        self.providers = []
        sf_key = os.environ.get("SILICONFLOW_API_KEY")
        if sf_key:
            self.providers.append({
                "name": "siliconflow",
                "url": "https://api.siliconflow.com/v1/chat/completions",
                "key": sf_key,
                "models": ["ByteDance-Seed/Seed-OSS-36B-Instruct", "deepseek-ai/DeepSeek-V3"],
            })
    
    def _call(self, provider_name, model, messages, max_tokens=500):
        for p in self.providers:
            if p["name"] != provider_name:
                continue
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.8,
                }
                req = urllib.request.Request(
                    p["url"],
                    data=json.dumps(payload).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {p['key']}",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    result = json.loads(r.read())
                    choice = result["choices"][0]["message"]
                    text = choice.get("content", "")
                    reasoning = choice.get("reasoning_content", "")
                    return {"text": text, "reasoning": reasoning,
                            "model": model, "provider": provider_name}
            except Exception as e:
                return {"error": str(e)}
        return {"error": f"Provider {provider_name} not configured"}
    
    def handle(self, action, payload, tile):
        if action == "generate":
            prompt = payload.get("prompt") or payload.get("text", "")
            model = payload.get("model", "ByteDance-Seed/Seed-OSS-36B-Instruct")
            provider = payload.get("provider", "siliconflow")
            max_tokens = payload.get("max_tokens", 500)
            messages = payload.get("messages", [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ])
            result = self._call(provider, model, messages, max_tokens)
            result["status"] = "ok" if "text" in result else "error"
            return result
        
        elif action == "list":
            return {
                "status": "ok",
                "providers": [
                    {"name": p["name"], "models": p["models"]}
                    for p in self.providers
                ]
            }
        
        return {"error": f"Unknown action: {action}"}
