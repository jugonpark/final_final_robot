"""NVIDIA Chat Completions adapter; the response is still untrusted."""
import re
import time
from urllib.parse import urlsplit

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from python_controller.llm.prompt import SYSTEM_PROMPT


DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NEMOTRON_ULTRA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
MODEL_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")


class NvidiaError(ValueError):
    pass


def _error_code(exc):
    if isinstance(exc, APITimeoutError):
        return "NVIDIA_TIMEOUT"
    if isinstance(exc, APIConnectionError):
        return "NVIDIA_NETWORK_ERROR"
    if isinstance(exc, APIStatusError):
        return f"NVIDIA_HTTP_{exc.status_code}"
    return "NVIDIA_REQUEST_FAILED"


def _completion_content(result):
    choices = getattr(result, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise NvidiaError("NVIDIA_INCOMPLETE_RESPONSE")
    choice = choices[0]
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    if getattr(choice, "finish_reason", None) != "stop" or getattr(message, "tool_calls", None):
        raise NvidiaError("NVIDIA_INCOMPLETE_RESPONSE")
    if not isinstance(content, str) or not content.strip():
        raise NvidiaError("NVIDIA_EMPTY_RESPONSE")
    return content


def _plan_messages(command):
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": command}]


class NvidiaPlanner:
    def __init__(self, *, api_key, base_url=DEFAULT_BASE_URL, model=None, enable_thinking=False):
        if not api_key or not api_key.strip():
            raise NvidiaError("NVIDIA_API_KEY_MISSING")
        if not model or not model.strip():
            raise NvidiaError("NVIDIA_MODEL_MISSING")
        if not MODEL_ID.fullmatch(model):
            raise NvidiaError("NVIDIA_MODEL_INVALID")
        api_key = api_key.strip()
        if api_key in model:
            raise NvidiaError("NVIDIA_MODEL_INVALID")
        try:
            endpoint = urlsplit(base_url)
            if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None
                    or endpoint.query or endpoint.fragment or api_key in base_url):
                raise ValueError
        except ValueError:
            raise NvidiaError("NVIDIA_BASE_URL_INVALID") from None
        if type(enable_thinking) is not bool or (enable_thinking and model != NEMOTRON_ULTRA_MODEL):
            raise NvidiaError("NVIDIA_THINKING_OPTION_INVALID")
        self.api_key, self.base_url, self.model = api_key, base_url, model
        self.enable_thinking = enable_thinking

    def _client(self):
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=20, max_retries=0)

    def _request_options(self, *, minimal=False):
        options = dict(model=self.model, stream=False, temperature=0.1, max_tokens=768)
        if self.model == NEMOTRON_ULTRA_MODEL:
            options.update(temperature=0.2, max_tokens=1024,
                           extra_body={"chat_template_kwargs": {"enable_thinking": self.enable_thinking}})
        if minimal:
            options["max_tokens"] = 32
        return options

    def plan(self, command):
        return self.complete(_plan_messages(command))

    def complete(self, messages):
        try:
            with self._client() as client:
                result = client.chat.completions.create(
                    messages=messages, **self._request_options(),
                )
        except (APIError, ValueError) as exc:
            raise NvidiaError(_error_code(exc)) from None
        content = _completion_content(result)
        if self.api_key in content:
            raise NvidiaError('NVIDIA_UNSAFE_RESPONSE')
        return content

    def stream_text(self, messages):
        """Yield only visible answer content from NVIDIA's completion stream."""
        options = {**self._request_options(), 'stream': True}
        finish = None
        try:
            with self._client() as client, client.chat.completions.create(messages=messages, **options) as stream:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if len(chunk.choices) != 1 or choice.delta.tool_calls or choice.delta.function_call:
                        raise NvidiaError('NVIDIA_INCOMPLETE_RESPONSE')
                    if choice.delta.content:
                        yield choice.delta.content
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except (APIError, ValueError) as exc:
            if isinstance(exc, NvidiaError):
                raise
            raise NvidiaError(_error_code(exc)) from None
        if finish != 'stop':
            raise NvidiaError('NVIDIA_INCOMPLETE_RESPONSE')

    def probe(self, command=None):
        """Measure a minimal request or a plan request without opening the robot executor."""
        minimal = command is None
        messages = [{"role": "user", "content": "Reply with just OK."}] if minimal else _plan_messages(command)
        row = dict(model=self.model, http_status=None, success=False,
                   returned_model=None, text=None, error_type=None, finish_reason=None,
                   usage=None, reasoning_present=None)
        start = time.monotonic()
        try:
            with self._client() as client:
                raw = client.chat.completions.with_raw_response.create(
                    messages=messages, **self._request_options(minimal=minimal),
                )
                row["http_status"] = raw.status_code
                result = raw.parse()
            returned_model = getattr(result, "model", None)
            if isinstance(returned_model, str):
                row["returned_model"] = returned_model.replace(self.api_key, "[redacted]")[:128]
            choices = getattr(result, "choices", None)
            if isinstance(choices, list) and len(choices) == 1:
                choice = choices[0]
                finish = getattr(choice, "finish_reason", None)
                if isinstance(finish, str):
                    row["finish_reason"] = finish.replace(self.api_key, "[redacted]")[:40]
                row["reasoning_present"] = bool(getattr(getattr(choice, "message", None), "reasoning_content", None))
            usage = getattr(result, "usage", None)
            if usage is not None:
                tokens = {name: getattr(usage, name, None)
                          for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
                tokens["reasoning_tokens"] = getattr(getattr(usage, "completion_tokens_details", None),
                                                     "reasoning_tokens", None)
                row["usage"] = {name: value if type(value) is int and value >= 0 else None
                                for name, value in tokens.items()}
            text = _completion_content(result)
            if not minimal and self.api_key in text:
                raise NvidiaError("NVIDIA_UNSAFE_RESPONSE")
            row.update(success=True, text=text.replace(self.api_key, "[redacted]")[:200] if minimal else text)
        except NvidiaError as exc:
            row["error_type"] = str(exc)
        except (APIError, ValueError) as exc:
            if isinstance(exc, APIStatusError):
                row["http_status"] = exc.status_code
            row["error_type"] = _error_code(exc)
        row["elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)
        return row
