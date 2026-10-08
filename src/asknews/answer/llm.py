"""The language model behind the answers.

One small interface, two implementations: Claude through the official SDK,
and a scripted stand-in for tests and offline development.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

DEFAULT_MODEL = "claude-opus-5-5"
Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")

# USD per million tokens (input, output). Used only to record an estimate
# per request; billing is what the provider says it is.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    refused: bool = False
    truncated: bool = False  # cut off at max_tokens: not a complete reply

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


class LLM(Protocol):
    model: str

    def complete(self, *, system: str, user: str, max_tokens: int) -> Completion: ...


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, PRICING[DEFAULT_MODEL])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class ClaudeLLM:
    """Claude via the Anthropic SDK. Credentials come from the environment."""

    def __init__(self, model: str = DEFAULT_MODEL, *, effort: str = "low") -> None:
        import anthropic

        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {EFFORT_LEVELS}, not {effort!r}")
        self._client = anthropic.Anthropic()
        self.model = model
        self.effort: Effort = EFFORT_LEVELS[EFFORT_LEVELS.index(effort)]

    def complete(self, *, system: str, user: str, max_tokens: int) -> Completion:
        from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam

        # The system prompt is identical across requests; cache it.
        system_block: TextBlockParam = {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
        message: MessageParam = {"role": "user", "content": user}
        config: OutputConfigParam = {"effort": self.effort}
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[system_block],
            messages=[message],
            output_config=config,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            refused=response.stop_reason == "refusal",
            truncated=response.stop_reason == "max_tokens",
        )


class ScriptedLLM:
    """Returns prepared replies in order; used by tests and offline runs."""

    def __init__(self, replies: list[str] | None = None, *, model: str = "scripted") -> None:
        self.replies = list(replies or [])
        self.model = model
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> Completion:
        del max_tokens  # a scripted reply has whatever length it has
        self.calls.append((system, user))
        if self.replies:
            text = self.replies.pop(0)
        else:
            text = '{"paragraphs": ["The sources describe the decision [1]."], "followups": []}'
        return Completion(text, self.model, len(user) // 4, len(text) // 4)
