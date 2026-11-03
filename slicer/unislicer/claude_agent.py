"""Claude as UniSlicer's assistant: answers questions about the plate, the print check and the settings, and
proposes setting changes the user applies with one click.

The user's own Anthropic API key is used (kept on this computer). Each question carries a short summary of the
current plate; nothing else leaves the computer. Proposed changes are never applied by Claude itself."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 4

SYSTEM = """You are the assistant inside UniSlicer, a slicer that dental labs use to prepare resin (LCD / DLP / \
laser SLA), PolyJet and FDM prints: models, supports, hollowing, exposure and the print check.

How to work:
- Answer in the language the user writes in (usually Hebrew). Be short and practical: a lab technician is at the printer.
- Every question comes with a "Current plate" block: printer, resin, quality, the settings that differ from the \
profile, the print-check results and the models. Base your answer on it; say so when something you need is missing.
- When a change of settings would help, call propose_settings with the exact setting keys from the block (or from \
the list of known keys) and the new values. The user sees each change as a button and decides; nothing is applied \
until they click it. Never claim you changed a setting.
- Only propose keys that exist. Keep values inside sensible ranges for the printer and resin. Prefer the resin \
maker's values when the profile has them, and explain any change in one short reason.
- For print failures (model stuck to the plate, layer shifts, holes, suction marks, parts that did not form), name \
the most likely cause first, then what to change and what to check on the printer.
- If a question is unrelated to printing or the program, answer briefly."""

PROPOSE_TOOL = {
    "name": "propose_settings",
    "description": ("Show the user setting changes as buttons they can apply. Use exact setting keys. "
                    "Nothing changes until the user clicks a button."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Exact setting key, e.g. bottom_exposure_time"},
                        "value": {"type": "string", "description": "New value as text: a number, true/false or an option name"},
                        "reason": {"type": "string", "description": "One short sentence: why this helps"},
                    },
                    "required": ["key", "value", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["changes"],
        "additionalProperties": False,
    },
}


@dataclass
class Proposal:
    key: str
    value: object
    reason: str = ""


@dataclass
class Reply:
    text: str = ""
    proposals: list[Proposal] = field(default_factory=list)
    error: str = ""                     # set when the request failed (shown instead of an answer)


def convert_value(key: str, raw):
    """A proposed value in the setting's own type, or None when the key or value is not valid."""
    from .settings import SCHEMA_BY_KEY
    meta = SCHEMA_BY_KEY.get(key)
    if meta is None:
        return None
    text = str(raw).strip()
    try:
        if meta.kind == "bool":
            low = text.lower()
            if low in ("true", "1", "yes", "on", "כן"):
                return True
            if low in ("false", "0", "no", "off", "לא"):
                return False
            return None
        if meta.kind == "int":
            v = int(round(float(text)))
        elif meta.kind == "float":
            v = float(text)
        elif meta.kind == "enum":
            names = [o[0] for o in (meta.options or ())]
            return text if text in names else None
        else:
            return text
    except (TypeError, ValueError):
        return None
    lo, hi = meta.minimum, meta.maximum
    if lo is not None and v < lo or hi is not None and v > hi:
        return None
    return v


def plate_context(state: dict) -> str:
    """The "Current plate" block sent with a question (compact, human-readable)."""
    lines = ["Current plate:"]
    for k in ("technology", "printer", "material", "quality"):
        if state.get(k):
            lines.append(f"- {k}: {state[k]}")
    if state.get("changed"):
        lines.append("- settings changed from the profile: " + ", ".join(f"{k}={v}" for k, v in state["changed"].items()))
    if state.get("key_values"):
        lines.append("- key settings: " + ", ".join(f"{k}={v}" for k, v in state["key_values"].items()))
    if state.get("models"):
        lines.append("- models: " + "; ".join(state["models"]))
    if state.get("checks"):
        lines.append("- print check: " + " | ".join(state["checks"]))
    if state.get("estimate"):
        lines.append(f"- estimate: {state['estimate']}")
    if state.get("known_keys"):
        lines.append("- other setting keys you may propose: " + ", ".join(state["known_keys"]))
    return "\n".join(lines)


def _api_message(exc) -> str:
    """The API's own error text (e.g. "Your credit balance is too low ...")."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(getattr(exc, "message", "") or "")


class ClaudeAssistant:
    """One conversation with Claude. ``ask`` blocks (run it off the UI thread)."""

    def __init__(self, api_key: str = "", model: str = MODEL, client=None):
        self.model = model
        self.messages: list = []         # append-only: thinking blocks stay valid
        self._level = 0
        self._client = client
        self._api_key = api_key

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        return self._client

    def reset(self):
        self.messages = []

    def ask(self, question: str, context: str = "") -> Reply:
        import anthropic
        user_text = f"{context}\n\nQuestion: {question}" if context else question
        self.messages.append({"role": "user", "content": user_text})
        reply = Reply()
        texts: list[str] = []
        try:
            client = self._get_client()
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._create(client)
                self.messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason == "refusal":
                    reply.error = "refusal"
                    break
                texts += [b.text for b in response.content if b.type == "text" and b.text.strip()]
                calls = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason != "tool_use" or not calls:
                    break
                results = []
                for call in calls:
                    shown, rejected = self._take_proposals(call, reply)
                    note = f"Shown to the user as {shown} button(s); they decide."
                    if rejected:
                        note += " Not shown (unknown key or invalid value): " + ", ".join(rejected)
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": note})
                self.messages.append({"role": "user", "content": results})
        except anthropic.AuthenticationError:
            reply.error = "auth"
        except anthropic.PermissionDeniedError:
            reply.error = "permission"
        except anthropic.RateLimitError:
            reply.error = "rate"
        except anthropic.APIStatusError as exc:
            msg = _api_message(exc)
            if "credit balance" in msg.lower():
                reply.error = "credit"
            else:
                reply.error = f"status:{exc.status_code}: {msg}" if msg else f"status:{exc.status_code}"
        except anthropic.APIConnectionError:
            reply.error = "network"
        if reply.error and self.messages and self.messages[-1]["role"] == "user" \
                and isinstance(self.messages[-1]["content"], str):
            self.messages.pop()          # the question never got an answer: it can be asked again
        reply.text = "\n\n".join(texts)
        return reply

    # request shapes, richest first: an account or model that rejects an optional feature (400) falls back to a
    # simpler request, remembered for the rest of the conversation
    _LEVELS = ("full", "no_fallback", "basic")

    def _create(self, client):
        import anthropic
        while True:
            level = self._LEVELS[self._level]
            try:
                if level == "full":
                    # a request a safety classifier declines is re-run on Anthropic's recommended fallback model
                    return client.beta.messages.create(
                        model=self.model, max_tokens=16000, system=SYSTEM, tools=[PROPOSE_TOOL],
                        messages=self.messages, output_config={"effort": "medium"},
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
                if level == "no_fallback":
                    return client.messages.create(
                        model=self.model, max_tokens=16000, system=SYSTEM, tools=[PROPOSE_TOOL],
                        messages=self.messages, output_config={"effort": "medium"})
                plain_tool = {k: v for k, v in PROPOSE_TOOL.items() if k != "strict"}
                return client.messages.create(model=self.model, max_tokens=16000, system=SYSTEM,
                                              tools=[plain_tool], messages=self.messages)
            except anthropic.BadRequestError as exc:
                if "credit balance" in _api_message(exc).lower() or self._level >= len(self._LEVELS) - 1:
                    raise
                self._level += 1

    @staticmethod
    def _take_proposals(call, reply: Reply) -> tuple[int, list[str]]:
        if call.name != "propose_settings":
            return 0, [call.name]
        data = call.input if isinstance(call.input, dict) else json.loads(call.input or "{}")
        shown, rejected = 0, []
        for c in data.get("changes", []) or []:
            key = str(c.get("key", "")).strip()
            value = convert_value(key, c.get("value"))
            if value is None:
                rejected.append(f"{key}={c.get('value')}")
                continue
            reply.proposals.append(Proposal(key, value, str(c.get("reason", ""))))
            shown += 1
        return shown, rejected
