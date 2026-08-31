"""LLM client interface and implementations."""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the response text."""
        ...

    @abstractmethod
    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Send a chat request expecting JSON response."""
        ...

    def extract_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        max_retries: int = 2,
    ) -> T:
        """
        Extract structured data into a Pydantic model.

        Implements self-correction loop: if parsing fails,
        re-prompts with the error up to max_retries times.
        """
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = self.chat_json(messages)
                return response_model.model_validate(response)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    # Add error feedback for self-correction
                    error_msg = f"JSON parsing/validation failed: {e}. Please fix and try again."
                    messages = [*messages, {"role": "user", "content": error_msg}]

        raise ValueError(f"Failed to extract structured data after {max_retries + 1} attempts") from last_error


class MockClient(LLMClient):
    """
    Deterministic mock LLM client for testing.

    Returns predictable responses based on input hashes,
    allowing fully offline operation and reproducible tests.
    """

    def __init__(self, responses: dict[str, str] | None = None):
        """
        Initialize mock client.

        Args:
            responses: Optional dict mapping input hashes to responses.
                       If not provided, uses built-in mock responses.
        """
        self._custom_responses = responses or {}
        self._call_count = 0

    def _hash_input(self, messages: list[dict[str, str]]) -> str:
        """Create a deterministic hash of the input messages."""
        content = json.dumps(messages, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Return mock response based on message content."""
        self._call_count += 1
        input_hash = self._hash_input(messages)

        # Check for custom response first
        if input_hash in self._custom_responses:
            return self._custom_responses[input_hash]

        # Analyze the last user message to determine response type
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "").lower()
                break

        # Return appropriate mock response based on context
        return self._generate_mock_response(last_user_msg)

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Return mock JSON response."""
        response = self.chat(messages, temperature, max_tokens)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # If response isn't JSON, wrap it
            return {"response": response}

    def _generate_mock_response(self, context: str) -> str:
        """Generate appropriate mock response based on context keywords."""

        # Invoice extraction context
        if "extract" in context and "invoice" in context:
            return self._mock_invoice_extraction(context)

        # Validation context
        if "validate" in context or "check" in context:
            return self._mock_validation_response(context)

        # Approval context
        if "approve" in context or "decision" in context:
            return self._mock_approval_response(context)

        # Critique context
        if "critique" in context or "review" in context:
            return self._mock_critique_response(context)

        # Default response
        return json.dumps({"status": "ok", "message": "Mock response"})

    def _mock_invoice_extraction(self, context: str) -> str:
        """Generate mock invoice extraction response."""
        # Default mock invoice
        invoice_data = {
            "invoice_number": "INV-MOCK-001",
            "vendor": {
                "name": "Mock Vendor Inc.",
                "address": "123 Test Street",
            },
            "date": "2026-01-15",
            "due_date": "2026-02-15",
            "line_items": [
                {"item": "WidgetA", "quantity": 5, "unit_price": 250.00},
                {"item": "WidgetB", "quantity": 3, "unit_price": 500.00},
            ],
            "subtotal": 2750.00,
            "tax_rate": 0.0,
            "tax_amount": 0.0,
            "total": 2750.00,
            "currency": "USD",
            "payment_terms": "Net 30",
        }

        # Adjust based on context hints
        if "1001" in context:
            invoice_data["invoice_number"] = "INV-1001"
            invoice_data["vendor"]["name"] = "Widgets Inc."
            invoice_data["line_items"] = [
                {"item": "WidgetA", "quantity": 10, "unit_price": 250.00},
                {"item": "WidgetB", "quantity": 5, "unit_price": 500.00},
            ]
            invoice_data["total"] = 5000.00
            invoice_data["subtotal"] = 5000.00

        elif "1002" in context:
            invoice_data["invoice_number"] = "INV-1002"
            invoice_data["vendor"]["name"] = "Gadgets Co."
            invoice_data["line_items"] = [
                {"item": "GadgetX", "quantity": 20, "unit_price": 750.00},
            ]
            invoice_data["total"] = 15000.00
            invoice_data["subtotal"] = 15000.00

        elif "1003" in context:
            invoice_data["invoice_number"] = "INV-1003"
            invoice_data["vendor"]["name"] = "Fraudster LLC"
            invoice_data["line_items"] = [
                {"item": "FakeItem", "quantity": 100, "unit_price": 1000.00},
            ]
            invoice_data["total"] = 100000.00
            invoice_data["subtotal"] = 100000.00

        elif "1008" in context or "supergizmo" in context or "megasprocket" in context:
            invoice_data["invoice_number"] = "INV-1008"
            invoice_data["vendor"]["name"] = "NoProd Industries"
            invoice_data["line_items"] = [
                {"item": "SuperGizmo", "quantity": 12, "unit_price": 400.00},
                {"item": "MegaSprocket", "quantity": 6, "unit_price": 850.00},
            ]
            invoice_data["total"] = 9900.00
            invoice_data["subtotal"] = 9900.00

        elif "1009" in context:
            invoice_data["invoice_number"] = "INV-1009"
            invoice_data["vendor"]["name"] = ""
            invoice_data["due_date"] = None
            invoice_data["line_items"] = [
                {"item": "WidgetA", "quantity": -5, "unit_price": 250.00},
                {"item": "WidgetB", "quantity": 2, "unit_price": 500.00},
            ]
            invoice_data["total"] = -250.00

        elif "1016" in context:
            invoice_data["invoice_number"] = "INV-1016"
            invoice_data["vendor"]["name"] = "Widgets Inc."
            invoice_data["line_items"] = [
                {"item": "WidgetA", "quantity": 4, "unit_price": 250.00},
                {"item": "WidgetB", "quantity": 2, "unit_price": 500.00},
                {"item": "WidgetC", "quantity": 3, "unit_price": 350.00},
            ]
            invoice_data["total"] = 3233.00

        return json.dumps(invoice_data)

    def _mock_validation_response(self, context: str) -> str:
        """Generate mock validation response."""
        return json.dumps({
            "is_valid": True,
            "flags": [],
            "message": "Validation passed",
        })

    def _mock_approval_response(self, context: str) -> str:
        """Generate mock approval decision."""
        return json.dumps({
            "status": "APPROVED",
            "reasoning": "Invoice meets all criteria. Vendor is known, items are in stock, and amount is within normal range.",
            "rules_applied": ["stock_check", "vendor_check", "amount_threshold"],
        })

    def _mock_critique_response(self, context: str) -> str:
        """Generate mock critique response."""
        return json.dumps({
            "accepted": True,
            "reasoning": "Decision is well-reasoned and follows all approval rules.",
            "suggested_changes": None,
        })

    @property
    def call_count(self) -> int:
        """Number of times the client has been called."""
        return self._call_count

    def reset(self) -> None:
        """Reset call count for testing."""
        self._call_count = 0


class GrokClient(LLMClient):
    """
    xAI Grok client using OpenAI-compatible API.

    Uses the openai SDK with xAI's base URL.
    API key should be set via XAI_API_KEY environment variable.
    """

    BASE_URL = "https://api.x.ai/v1"
    DEFAULT_MODEL = "grok-4.6"  # Available: grok-4.3, grok-4.5, grok-4.6

    def __init__(self, api_key: str | None = None, model: str | None = None):
        """
        Initialize Grok client.

        Args:
            api_key: xAI API key. Falls back to XAI_API_KEY env var.
            model: Model name. Defaults to grok-beta.
        """
        self._api_key = api_key or os.getenv("XAI_API_KEY")
        self._model = model or self.DEFAULT_MODEL
        self._client: Any = None  # Lazy init

    def _get_client(self) -> Any:
        """Lazily initialize OpenAI client with xAI base URL."""
        if self._client is None:
            if not self._api_key:
                raise ValueError(
                    "xAI API key not found. Set XAI_API_KEY environment variable "
                    "or pass api_key to GrokClient."
                )
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ImportError("openai package required for GrokClient: pip install openai") from e

            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self.BASE_URL,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send chat completion to Grok."""
        client = self._get_client()
        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Send chat request expecting JSON response."""
        # Add JSON instruction to system message if not present
        json_instruction = "Respond with valid JSON only. No markdown, no explanations."

        enhanced_messages = list(messages)
        if enhanced_messages and enhanced_messages[0].get("role") == "system":
            enhanced_messages[0] = {
                "role": "system",
                "content": f"{enhanced_messages[0]['content']}\n\n{json_instruction}",
            }
        else:
            enhanced_messages.insert(0, {"role": "system", "content": json_instruction})

        response = self.chat(enhanced_messages, temperature, max_tokens)

        # Strip markdown code blocks if present
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()

        return json.loads(response)


class OpenRouterClient(LLMClient):
    """
    OpenRouter client for failover support.

    OpenRouter provides access to multiple models through a single API.
    API key should be set via OPENROUTER_API_KEY environment variable.
    """

    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self._model = model or self.DEFAULT_MODEL
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ValueError(
                    "OpenRouter API key not found. Set OPENROUTER_API_KEY environment variable."
                )
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ImportError("openai package required: pip install openai") from e

            self._client = OpenAI(api_key=self._api_key, base_url=self.BASE_URL)
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        json_instruction = "Respond with valid JSON only. No markdown, no explanations."
        enhanced_messages = list(messages)
        if enhanced_messages and enhanced_messages[0].get("role") == "system":
            enhanced_messages[0] = {
                "role": "system",
                "content": f"{enhanced_messages[0]['content']}\n\n{json_instruction}",
            }
        else:
            enhanced_messages.insert(0, {"role": "system", "content": json_instruction})

        response = self.chat(enhanced_messages, temperature, max_tokens)
        response = response.strip()
        for prefix in ("```json", "```"):
            if response.startswith(prefix):
                response = response[len(prefix):]
        if response.endswith("```"):
            response = response[:-3]
        return json.loads(response.strip())


class FailoverClient(LLMClient):
    """
    Client wrapper that tries multiple providers in order.

    If the primary provider fails, automatically switches to fallback.
    Uses sticky switching: once failover occurs, stays on fallback.
    """

    def __init__(self, providers: list[LLMClient]):
        if not providers:
            raise ValueError("At least one provider required")
        self._providers = providers
        self._current_index = 0

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        return self._try_providers("chat", messages, temperature, max_tokens)

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        return self._try_providers("chat_json", messages, temperature, max_tokens)

    def _try_providers(self, method: str, *args, **kwargs) -> Any:
        """Try each provider starting from current index."""
        last_error: Exception | None = None

        for i in range(len(self._providers)):
            idx = (self._current_index + i) % len(self._providers)
            provider = self._providers[idx]

            try:
                result = getattr(provider, method)(*args, **kwargs)
                # Sticky switch on success after failover
                if idx != self._current_index:
                    self._current_index = idx
                return result
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"All {len(self._providers)} providers failed") from last_error


class RecordingClient(LLMClient):
    """
    Client wrapper that records all LLM responses for replay.

    Responses are saved to a JSON file keyed by input hash.
    Use with ReplayClient for zero-cost demos/tests.
    """

    def __init__(self, wrapped: LLMClient, recording_path: str = "data/llm_recordings.json"):
        self._wrapped = wrapped
        self._recording_path = recording_path
        self._recordings: dict[str, Any] = self._load_recordings()

    def _load_recordings(self) -> dict[str, Any]:
        from pathlib import Path
        path = Path(self._recording_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_recordings(self) -> None:
        from pathlib import Path
        path = Path(self._recording_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self._recordings, f, indent=2)

    def _hash_input(self, messages: list[dict[str, str]], method: str) -> str:
        content = json.dumps({"method": method, "messages": messages}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        response = self._wrapped.chat(messages, temperature, max_tokens)
        key = self._hash_input(messages, "chat")
        self._recordings[key] = {"type": "chat", "response": response}
        self._save_recordings()
        return response

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        response = self._wrapped.chat_json(messages, temperature, max_tokens)
        key = self._hash_input(messages, "chat_json")
        self._recordings[key] = {"type": "chat_json", "response": response}
        self._save_recordings()
        return response


class ReplayClient(LLMClient):
    """
    Client that replays recorded LLM responses.

    Zero API cost for demos and deterministic testing.
    Falls back to wrapped client if recording not found.
    """

    def __init__(
        self,
        recording_path: str = "data/llm_recordings.json",
        fallback: LLMClient | None = None,
        strict: bool = False,
    ):
        self._recording_path = recording_path
        self._fallback = fallback
        self._strict = strict  # If True, raise error on cache miss
        self._recordings = self._load_recordings()

    def _load_recordings(self) -> dict[str, Any]:
        from pathlib import Path
        path = Path(self._recording_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def _hash_input(self, messages: list[dict[str, str]], method: str) -> str:
        content = json.dumps({"method": method, "messages": messages}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        key = self._hash_input(messages, "chat")
        if key in self._recordings:
            return self._recordings[key]["response"]
        if self._strict:
            raise KeyError(f"No recording found for hash {key}")
        if self._fallback:
            return self._fallback.chat(messages, temperature, max_tokens)
        raise KeyError(f"No recording found for hash {key} and no fallback configured")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        key = self._hash_input(messages, "chat_json")
        if key in self._recordings:
            return self._recordings[key]["response"]
        if self._strict:
            raise KeyError(f"No recording found for hash {key}")
        if self._fallback:
            return self._fallback.chat_json(messages, temperature, max_tokens)
        raise KeyError(f"No recording found for hash {key} and no fallback configured")


def get_client(backend: str | None = None) -> LLMClient:
    """
    Factory function to get the appropriate LLM client.

    Args:
        backend: Client mode. Options:
            - "mock": Deterministic mock responses (default)
            - "grok": xAI Grok API
            - "openrouter": OpenRouter API
            - "failover": Grok with OpenRouter fallback
            - "record": Record responses from Grok
            - "replay": Replay recorded responses

    Returns:
        Configured LLMClient instance.
    """
    backend = backend or os.getenv("LLM_BACKEND", "mock")

    if backend == "mock":
        return MockClient()
    elif backend == "grok":
        return GrokClient()
    elif backend == "openrouter":
        return OpenRouterClient()
    elif backend == "failover":
        return FailoverClient([GrokClient(), OpenRouterClient()])
    elif backend == "record":
        return RecordingClient(GrokClient())
    elif backend == "replay":
        return ReplayClient(fallback=MockClient())
    else:
        raise ValueError(
            f"Unknown LLM backend: {backend}. "
            "Use 'mock', 'grok', 'openrouter', 'failover', 'record', or 'replay'."
        )
