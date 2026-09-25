from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Any


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"


def embedding_status() -> dict[str, Any]:
    provider = os.environ.get("DOCUMENT_EMBEDDING_PROVIDER", "off").lower()
    if provider == "off":
        return {"enabled": False, "provider": "off", "model": None}
    if _flag_embedding_available():
        return {"enabled": True, "provider": "FlagEmbedding", "model": _model_name()}
    if _sentence_transformers_available():
        return {"enabled": True, "provider": "sentence-transformers", "model": _model_name()}
    return {
        "enabled": False,
        "provider": "missing",
        "model": _model_name(),
        "message": "Installe sentence-transformers ou FlagEmbedding pour activer BGE-M3.",
    }


def embed_text(text: str) -> list[float] | None:
    if not text.strip() or os.environ.get("DOCUMENT_EMBEDDING_PROVIDER", "off").lower() == "off":
        return None
    text = text[:24_000]
    try:
        if _flag_embedding_available():
            model = _flag_embedding_model()
            output = model.encode([text], batch_size=1, max_length=8192)
            dense = output.get("dense_vecs", output) if isinstance(output, dict) else output
            return _to_float_list(dense[0] if hasattr(dense, "__getitem__") else dense)
        if _sentence_transformers_available():
            model = _sentence_transformer_model()
            return _to_float_list(model.encode([text], normalize_embeddings=True)[0])
    except Exception:
        return None
    return None


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    return dot / max(norm_left * norm_right, 1e-9)


def _model_name() -> str:
    return os.environ.get("BGE_MODEL", DEFAULT_EMBEDDING_MODEL)


def _flag_embedding_available() -> bool:
    try:
        import FlagEmbedding  # noqa: F401

        return True
    except Exception:
        return False


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _flag_embedding_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(_model_name(), use_fp16=False)


@lru_cache(maxsize=1)
def _sentence_transformer_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(_model_name())


def _to_float_list(values: Any) -> list[float]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    return [float(value) for value in values]
