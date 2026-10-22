"""Collecteur Hacker News via l'API Algolia (pas de clé nécessaire)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mnews.collectors.base import RawItem, http_get

API = "https://hn.algolia.com/api/v1/search_by_date"

# Mots-clés qui signalent un sujet IA dans le titre
AI_KEYWORDS = [
    "ai", "a.i.", "llm", "llms", "gpt", "chatgpt", "openai", "claude", "anthropic",
    "gemini", "deepmind", "mistral", "llama", "copilot", "codex", "cursor", "agent",
    "agents", "agentic", "transformer", "diffusion", "machine learning", "deep learning",
    "neural", "model", "models", "inference", "fine-tun", "rag", "embedding", "mcp",
    "hugging face", "huggingface", "stable diffusion", "midjourney", "sora", "grok",
    "xai", "nvidia", "gpu", "tpu", "token", "prompt", "vision-language", "multimodal",
    "whisper", "text-to-speech", "speech", "ollama", "vllm", "lora", "qwen", "deepseek",
]


def _looks_ai(title: str) -> bool:
    t = f" {title.lower()} "
    for kw in AI_KEYWORDS:
        if f" {kw} " in t or f" {kw}s " in t or f"{kw}" in t and len(kw) > 5:
            return True
    return False


def collect_hackernews(since: datetime, min_points: int = 40, pages: int = 3) -> list[RawItem]:
    since_ts = int(since.replace(tzinfo=timezone.utc).timestamp())
    items: list[RawItem] = []
    seen: set[str] = set()
    for page in range(pages):
        resp = http_get(
            API,
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{since_ts},points>{min_points}",
                "hitsPerPage": 100,
                "page": page,
            },
        )
        data = resp.json()
        for hit in data.get("hits", []):
            title = hit.get("title") or ""
            if not title or not _looks_ai(title):
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            if url in seen:
                continue
            seen.add(url)
            created = hit.get("created_at_i")
            published = (
                datetime.fromtimestamp(created, tz=timezone.utc).replace(tzinfo=None) if created else None
            )
            items.append(
                RawItem(
                    url=url,
                    title=title,
                    summary=(hit.get("story_text") or "")[:600],
                    published_at=published,
                    score=int(hit.get("points") or 0),
                    lang="en",
                    extra={
                        "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        "comments": hit.get("num_comments", 0),
                    },
                )
            )
        if page + 1 >= int(data.get("nbPages", 1)):
            break
    return items
