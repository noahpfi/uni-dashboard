"""Shared Groq client factory with key rotation on rate/quota errors."""
import os

from groq import APIStatusError, Groq, RateLimitError

GROQ_MODEL = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')

_active_idx = 0


def make_clients() -> list[Groq]:
    keys = [k.strip() for k in os.environ['GROQ_API_KEYS'].split(',') if k.strip()]
    return [Groq(api_key=k) for k in keys]


def groq_chat(clients: list[Groq], **kwargs):
    global _active_idx
    last_exc = None
    for i in range(_active_idx, len(clients)):
        try:
            return clients[i].chat.completions.create(**kwargs)
        except (RateLimitError, APIStatusError) as e:
            last_exc = e
            status = getattr(e, 'status_code', None)
            if status not in (429, 413):
                raise
            _active_idx = i + 1
            print(f'    [groq] key {i + 1}/{len(clients)} exhausted ({status}), rotating...')
    raise last_exc
