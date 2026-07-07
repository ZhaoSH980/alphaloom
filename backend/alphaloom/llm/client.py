"""LLM config from .env and the production transport (openai SDK)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from pydantic import BaseModel

Transport = Callable[[dict[str, Any]], dict[str, Any]]

# Offline replay needs no real endpoint: a fresh clone without a .env must
# still serve the committed recorded runs (README quick start). The model
# default MUST match the recordings in llm_calls.sqlite — replay keys embed
# the model string, so any other value would miss the cache.
OFFLINE_DEFAULTS = {
    "LLM_BASE_URL": "http://offline.invalid/v1",
    "LLM_API_KEY": "offline-replay",
    "LLM_MODEL": "spark-x1",
}

LIVE_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


class LLMConfigError(RuntimeError):
    pass


def _dotenv_candidates(dotenv_path: Path | None) -> list[Path]:
    if dotenv_path is not None:
        return [Path(dotenv_path)]
    cwd = Path.cwd()
    repo = Path(__file__).resolve().parents[3]
    candidates = [
        cwd / ".env",
        cwd / "backend" / ".env",
        repo / ".env",
        repo / "backend" / ".env",
    ]
    unique = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def _load_dotenv_files(dotenv_path: Path | None,
                       *, override: bool = False) -> list[Path]:
    checked = _dotenv_candidates(dotenv_path)
    for path in checked:
        if path.is_file():
            load_dotenv(path, override=override)
    return checked


class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None,
                 offline: bool | None = None,
                 dotenv_override: bool = False) -> "LLMConfig":
        if offline is None:
            offline = os.environ.get("ALPHALOOM_OFFLINE", "") == "1"
        if offline:
            return cls(
                base_url=OFFLINE_DEFAULTS["LLM_BASE_URL"],
                api_key=OFFLINE_DEFAULTS["LLM_API_KEY"],
                model=OFFLINE_DEFAULTS["LLM_MODEL"],
            )
        checked = _load_dotenv_files(dotenv_path, override=dotenv_override)
        missing = [key for key in LIVE_KEYS if not os.environ.get(key)]
        if missing:
            paths = ", ".join(str(path) for path in checked)
            raise LLMConfigError(
                "live LLM mode is missing "
                f"{', '.join(missing)}. Checked dotenv paths: {paths}")
        return cls(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
        )


def openai_transport(config: LLMConfig) -> Transport:
    from openai import OpenAI

    client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    def send(request: dict[str, Any]) -> dict[str, Any]:
        response = client.chat.completions.create(**request)
        return response.model_dump()

    return send
