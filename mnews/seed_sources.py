"""Sources initiales. Modifiables ensuite depuis l'interface web."""
from __future__ import annotations

import json

from sqlalchemy import select

from mnews.db import Source, session_scope

# (nom, type, url, langue, config, actif)
# Toutes les URL ci-dessous ont été vérifiées le 22/10/2026.
SEED: list[tuple[str, str, str, str, dict, bool]] = [
    # --- Blogs officiels ---
    ("Anthropic - News", "html_links", "https://www.anthropic.com/news", "en",
     {"pattern": r"^/news/[^/]+$", "exclude": ["/news/announcements", "/news/features", "/news/product", "/news/policy", "/news/research"]}, True),
    ("Anthropic - Claude Code releases", "rss", "https://github.com/anthropics/claude-code/releases.atom", "en", {}, True),
    ("OpenAI - News", "rss", "https://openai.com/news/rss.xml", "en", {}, True),
    ("Google DeepMind - Blog", "rss", "https://deepmind.google/blog/rss.xml", "en", {}, True),
    ("Google - The Keyword (AI)", "rss", "https://blog.google/technology/ai/rss/", "en", {}, True),
    ("Hugging Face - Blog", "rss", "https://huggingface.co/blog/feed.xml", "en", {}, True),
    ("Mistral AI - News", "rss", "https://mistral.ai/rss.xml", "en", {}, True),
    ("Microsoft - Source (AI)", "rss", "https://news.microsoft.com/source/topics/ai/feed/", "en", {}, True),
    ("Microsoft 365 - Blog", "rss", "https://www.microsoft.com/en-us/microsoft-365/blog/feed/", "en", {}, True),
    ("Meta - Engineering (AI research)", "rss", "https://engineering.fb.com/category/ai-research/feed/", "en", {}, True),
    ("NVIDIA - Blog", "rss", "https://blogs.nvidia.com/feed/", "en", {}, True),
    ("Cloudflare - Blog (AI)", "rss", "https://blog.cloudflare.com/tag/ai/rss/", "en", {}, True),
    # --- Médias et newsletters (EN) ---
    ("The Verge - AI", "rss", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "en", {}, True),
    ("TechCrunch - AI", "rss", "https://techcrunch.com/category/artificial-intelligence/feed/", "en", {}, True),
    ("Ars Technica - AI", "rss", "https://arstechnica.com/ai/feed/", "en", {}, True),
    ("MIT Technology Review - AI", "rss", "https://www.technologyreview.com/topic/artificial-intelligence/feed", "en", {}, True),
    ("VentureBeat - AI", "rss", "https://news.google.com/rss/search?q=site:venturebeat.com&hl=en-US&gl=US&ceid=US:en", "en", {}, True),
    ("The Information", "rss", "https://www.theinformation.com/feed", "en", {}, True),
    ("The Decoder", "rss", "https://the-decoder.com/feed/", "en", {}, True),
    ("Simon Willison", "rss", "https://simonwillison.net/atom/everything/", "en", {}, True),
    ("Latent Space", "rss", "https://www.latent.space/feed", "en", {}, True),
    ("One Useful Thing (Ethan Mollick)", "rss", "https://www.oneusefulthing.org/feed", "en", {}, True),
    # --- Médias FR ---
    ("ActuIA", "rss", "https://www.actuia.com/feed/", "fr", {}, True),
    ("Numerama - Tech", "rss", "https://www.numerama.com/tech/feed/", "fr", {}, True),
    ("Le Monde Informatique - IA", "rss", "https://www.lemondeinformatique.fr/flux-rss/thematique/intelligence-artificielle/rss.xml", "fr", {}, True),
    ("Frandroid", "rss", "https://www.frandroid.com/feed", "fr", {}, True),
    ("01net", "rss", "https://news.google.com/rss/search?q=site:01net.com&hl=fr&gl=FR&ceid=FR:fr", "fr", {}, True),
    ("Siècle Digital", "rss", "https://siecledigital.fr/feed/", "fr", {}, True),
    ("Blog du Modérateur", "rss", "https://www.blogdumoderateur.com/feed/", "fr", {}, True),
    ("L'Usine Digitale", "rss", "https://www.usine-digitale.fr/rss", "fr", {}, True),
    ("Journal du Net - Web & Tech", "rss", "https://www.journaldunet.com/web-tech/rss/", "fr", {}, True),
    # --- Agrégateurs ---
    ("Hacker News (IA)", "hackernews", "https://hn.algolia.com/", "en", {"min_points": 40}, True),
    ("Reddit r/artificial", "reddit", "https://www.reddit.com/r/artificial/top.rss?t=day", "en", {}, True),
    ("Reddit r/LocalLLaMA", "reddit", "https://www.reddit.com/r/LocalLLaMA/top.rss?t=day", "en", {}, True),
    ("Reddit r/ClaudeAI", "reddit", "https://www.reddit.com/r/ClaudeAI/top.rss?t=day", "en", {}, True),
    ("Reddit r/MachineLearning", "reddit", "https://www.reddit.com/r/MachineLearning/top.rss?t=day", "en", {}, True),
    ("Reddit r/OpenAI", "reddit", "https://www.reddit.com/r/OpenAI/top.rss?t=day", "en", {}, True),
    # --- GitHub ---
    ("GitHub trending (semaine)", "github_trending", "https://github.com/trending?since=weekly", "en", {}, True),
    ("GitHub nouveaux dépôts populaires", "github_search", "https://api.github.com/search/repositories", "en",
     {"days": 7, "min_stars": 150, "topics": ["ai", "llm", "agents", "claude", "mcp", "openai", "ai-agents"]}, True),
    # --- Comptes X : désactivés par défaut, aucune passerelle RSS gratuite ne fonctionne
    #     au 22/10/2026 (nitter.net, xcancel, rsshub). À activer dans l'interface si une
    #     passerelle redevient disponible (X_RSS_TEMPLATE dans .env).
    ("X @AnthropicAI", "x", "AnthropicAI", "en", {"handle": "AnthropicAI"}, False),
    ("X @claudeai", "x", "claudeai", "en", {"handle": "claudeai"}, False),
    ("X @OpenAI", "x", "OpenAI", "en", {"handle": "OpenAI"}, False),
    ("X @GoogleDeepMind", "x", "GoogleDeepMind", "en", {"handle": "GoogleDeepMind"}, False),
    ("X @MistralAI", "x", "MistralAI", "en", {"handle": "MistralAI"}, False),
    ("X @huggingface", "x", "huggingface", "en", {"handle": "huggingface"}, False),
    ("X @karpathy", "x", "karpathy", "en", {"handle": "karpathy"}, False),
    ("X @sama", "x", "sama", "en", {"handle": "sama"}, False),
]


# Anciennes URL remplacées : seed_sources() met à jour les sources déjà en base.
# VentureBeat (anti-robots Vercel) et 01net (Cloudflare bloque l'IP de l'hébergeur)
# passent par le flux Google News du site.
REPLACED_URLS = {
    "https://venturebeat.com/category/ai/feed/": "https://news.google.com/rss/search?q=site:venturebeat.com&hl=en-US&gl=US&ceid=US:en",
    "https://www.01net.com/feed/": "https://news.google.com/rss/search?q=site:01net.com&hl=fr&gl=FR&ceid=FR:fr",
}


def seed_sources() -> int:
    """Insère les sources manquantes et met à jour les URL remplacées (idempotent).

    Renvoie le nombre de sources ajoutées.
    """
    added = 0
    with session_scope() as session:
        sources = session.scalars(select(Source)).all()
        for src in sources:
            if src.url in REPLACED_URLS:
                src.url = REPLACED_URLS[src.url]
                src.last_status = "never"
                src.last_error = None
        existing = {s.url for s in sources}
        for name, type_, url, lang, cfg, enabled in SEED:
            if url in existing:
                continue
            session.add(Source(name=name, type=type_, url=url, lang=lang, enabled=enabled,
                               config_json=json.dumps(cfg, ensure_ascii=False)))
            added += 1
    return added
