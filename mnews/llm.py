"""Enveloppe autour du SDK Anthropic : appels JSON structurés + suivi des coûts."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from mnews.config import settings
from mnews.costs import check_budget, record_call

log = logging.getLogger("mnews.llm")

# Tarifs $/1M tokens : (entrée, sortie, lecture du cache).
# L'écriture dans le cache (TTL 5 min) coûte 1,25 x le tarif d'entrée.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
}
CACHE_WRITE_MULTIPLIER = 1.25
# Modèle inconnu (ex. modèle de repli) : on compte au tarif le plus élevé pour ne pas sous-estimer
DEFAULT_PRICE = PRICES["claude-fable-5-1"]


@dataclass
class LLMResult:
    data: dict
    input_tokens: int  # total, cache compris
    output_tokens: int
    cost_usd: float
    model: str
    request_id: str = ""


class LLMRefusal(Exception):
    pass


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        settings.check_required("anthropic_api_key")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=600.0)
    return _client


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Coût en $. `input_tokens` = tokens d'entrée hors cache (usage.input_tokens)."""
    price_in, price_out, price_cache_read = PRICES.get(model, DEFAULT_PRICE)
    return (
        input_tokens * price_in
        + cache_write_tokens * price_in * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * price_cache_read
        + output_tokens * price_out
    ) / 1_000_000


def call_json(system: str, user: str, schema: dict, step: str, effort: str = "medium",
              max_tokens: int = 16000) -> LLMResult:
    """Appelle Claude et renvoie un dict validé par le schéma JSON (structured outputs).

    Les refus de sécurité sont redirigés côté serveur vers un modèle de repli
    (paramètre `fallbacks="default"`), ce qui évite qu'une newsletter saute pour rien.
    Chaque appel est enregistré (table llm_calls), y compris ceux qui échouent après
    facturation (réponse tronquée, refus, JSON invalide).
    """
    check_budget()
    client = get_client()
    model = settings.claude_model
    log.info("Appel %s (effort=%s, %d caractères)", model, effort, len(user))

    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        ) as stream:
            response = stream.get_final_message()
    except anthropic.APIError as exc:
        # Requête rejetée ou interrompue : rien de facturable connu, on garde une trace
        record_call(step, model, "error", error=f"{type(exc).__name__}: {exc}"[:2000])
        raise

    usage = response.usage
    fresh_in = int(getattr(usage, "input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    served_model = getattr(response, "model", model) or model
    cost = estimate_cost(served_model, fresh_in, output_tokens, cache_write, cache_read)
    request_id = getattr(response, "_request_id", "") or ""
    log.info("Réponse %s : %d tokens entrée (dont cache : %d écrits, %d lus), %d sortie, ~%.4f $",
             served_model, fresh_in + cache_write + cache_read, cache_write, cache_read,
             output_tokens, cost)

    def record(status: str, error: str | None = None) -> None:
        record_call(step, served_model, status, fresh_in, cache_write, cache_read,
                    output_tokens, cost, request_id, error)

    try:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMRefusal(f"Refus du modèle : {getattr(details, 'explanation', '') or details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Réponse tronquée (max_tokens atteint)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON invalide renvoyé par le modèle : {exc}") from exc
    except Exception as exc:
        record("error", str(exc)[:2000])
        raise
    record("ok")

    return LLMResult(
        data=data,
        input_tokens=fresh_in + cache_write + cache_read,
        output_tokens=output_tokens,
        cost_usd=cost,
        model=served_model,
        request_id=request_id,
    )
