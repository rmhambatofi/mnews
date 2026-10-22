"""Enveloppe autour du SDK Anthropic : appels JSON structurés + suivi des coûts."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from mnews.config import settings

log = logging.getLogger("mnews.llm")

# Tarifs $/1M tokens (entrée, sortie) pour l'estimation du coût
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
}


@dataclass
class LLMResult:
    data: dict
    input_tokens: int
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


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, (4.0, 20.0))
    return input_tokens * price_in / 1_000_000 + output_tokens * price_out / 1_000_000


def call_json(system: str, user: str, schema: dict, effort: str = "medium",
              max_tokens: int = 16000) -> LLMResult:
    """Appelle Claude et renvoie un dict validé par le schéma JSON (structured outputs).

    Les refus de sécurité sont redirigés côté serveur vers un modèle de repli
    (paramètre `fallbacks="default"`), ce qui évite qu'une newsletter saute pour rien.
    """
    client = get_client()
    model = settings.claude_model
    log.info("Appel %s (effort=%s, %d caractères)", model, effort, len(user))

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

    usage = response.usage
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0) \
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    served_model = getattr(response, "model", model) or model
    cost = estimate_cost(served_model, input_tokens, output_tokens)
    log.info("Réponse %s : %d tokens entrée, %d sortie, ~%.4f $",
             served_model, input_tokens, output_tokens, cost)
    return LLMResult(
        data=data,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        model=served_model,
        request_id=getattr(response, "_request_id", "") or "",
    )
