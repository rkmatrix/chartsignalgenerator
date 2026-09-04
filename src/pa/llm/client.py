from __future__ import annotations

from pathlib import Path

import httpx


class LLMClient:
    """Optional OpenAI consult. Empty key → local heuristic. Never places orders."""

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini", prompt_file: Path | None = None) -> None:
        self.api_key = api_key.strip()
        self.model = model
        self.prompt_file = prompt_file
        self.system_prompt = "You are a paper-trading desk assistant. Do not instruct live orders."

    def reload_prompt(self) -> str:
        if self.prompt_file and self.prompt_file.exists():
            self.system_prompt = self.prompt_file.read_text(encoding="utf-8")
        return self.system_prompt

    def local_reply(self, question: str, context: str) -> str:
        q = question.lower()
        if "why" in q or "signal" in q:
            return f"Latest desk context:\n{context[:1500]}"
        if "risk" in q:
            return "Risk gates: paper lock, daily drawdown halt, max positions, session buffers, kill switch."
        return f"(local LLM fallback) I can only discuss paper desk state. Context:\n{context[:800]}"

    async def ask(self, question: str, context: str) -> str:
        self.reload_prompt()
        if not self.api_key:
            return self.local_reply(question, context)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
                        ],
                        "temperature": 0.2,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            return self.local_reply(question, context + f"\n(llm_error: {exc})")
