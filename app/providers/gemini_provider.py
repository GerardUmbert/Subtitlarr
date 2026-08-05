import httpx

from app.providers.base import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderStatus,
    TranslationProvider,
)
from app.providers.prompts import build_system_prompt, build_user_prompt

# REST-only client (no google-generativeai SDK) to avoid that SDK's grpc
# dependency, which has inconsistent musl/alpine wheel availability — see
# the Dockerfile risk notes.
_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(TranslationProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(base_url=_API_BASE, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        system_prompt = build_system_prompt(source_lang, target_lang)
        try:
            resp = await self._client.post(
                f"/models/{self._model}:generateContent",
                params={"key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [
                        {"role": "user", "parts": [{"text": build_user_prompt(dialogue_text)}]}
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderRateLimitedError(f"Gemini request timed out: {exc}") from exc

        if resp.status_code == 429:
            raise ProviderRateLimitedError("Gemini rate limit hit (429)")
        if resp.status_code != 200:
            raise ProviderError(f"Gemini request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected Gemini response shape: {data}") from exc

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.get("/models", params={"key": self._api_key})
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}: {resp.text}")
        return ProviderStatus(ok=True, detail="API key valid")
