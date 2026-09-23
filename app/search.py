from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .semantic import cosine_similarity, embed_text


STOP_WORDS = {
    "a",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "dans",
    "de",
    "des",
    "du",
    "en",
    "et",
    "la",
    "le",
    "les",
    "ou",
    "par",
    "pour",
    "sur",
    "un",
    "une",
}

SEMANTIC_HINTS = {
    "morsure": {"attaque", "blessure", "chien", "animal", "responsabilite"},
    "chien": {"animal", "canin", "proprietaire", "attaque", "morsure"},
    "recouvrement": {"creance", "impaye", "facture", "relance", "paiement"},
    "facture": {"paiement", "impaye", "creance", "fournisseur"},
    "licenciement": {"rupture", "travail", "salarie", "employeur"},
    "contrat": {"accord", "clause", "obligation", "signature"},
    "assurance": {"garantie", "sinistre", "indemnisation", "police"},
}


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9']+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in STOP_WORDS]


def expand_query(tokens: list[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        expanded.update(SEMANTIC_HINTS.get(token, set()))
    return expanded


def search_documents(store: dict[str, Any], user: dict[str, Any], params: dict[str, str]) -> list[dict[str, Any]]:
    query = params.get("q", "").strip()
    if not query:
        return []

    allowed_matter_ids = set(user.get("allowed_matter_ids", []))
    allowed_client_ids = set(user.get("allowed_client_ids", []))
    docs = {
        doc["id"]: doc
        for doc in store["documents"]
        if _can_access_doc(doc, allowed_matter_ids, allowed_client_ids) and doc.get("status") == "indexed"
    }
    matters = {matter["id"]: matter for matter in store["matters"]}
    clients = {client["id"]: client for client in store["clients"]}

    query_tokens = tokenize(query)
    expanded = expand_query(query_tokens)
    query_counter = Counter(query_tokens)
    query_embedding = embed_text(query)
    results = []

    for page in store["pages"]:
        doc = docs.get(page["document_id"])
        if not doc or not _matches_filters(doc, params):
            continue
        text = page.get("text", "")
        tokens = tokenize(text)
        if not tokens:
            continue

        token_counter = Counter(tokens)
        exact_score = _cosine(query_counter, token_counter)
        semantic_hits = sum(1 for token in tokens if token in expanded)
        hint_score = semantic_hits / max(len(set(tokens)), 1)
        vector_score = cosine_similarity(query_embedding, page.get("embedding"))
        semantic_score = max(hint_score, vector_score)
        phrase_bonus = 0.25 if query.lower() in text.lower() else 0.0
        if query_embedding and page.get("embedding"):
            score = exact_score * 0.35 + semantic_score * 0.55 + hint_score * 0.1 + phrase_bonus
        else:
            score = exact_score * 0.65 + semantic_score * 0.35 + phrase_bonus

        if score <= 0:
            continue

        matter = matters.get(doc["matter_id"], {})
        client = clients.get(doc["client_id"], {})
        results.append(
            {
                "score": round(score, 4),
                "semantic_score": round(semantic_score, 4),
                "exact_score": round(exact_score, 4),
                "document_id": doc["id"],
                "filename": doc["filename"],
                "document_type": doc.get("type", ""),
                "status": doc["status"],
                "page_number": page["page_number"],
                "excerpt": make_excerpt(text, query_tokens, expanded),
                "client": client.get("name", ""),
                "matter": matter.get("name", ""),
                "matter_id": matter.get("id", ""),
                "client_id": client.get("id", ""),
            }
        )

    return sorted(results, key=lambda item: item["score"], reverse=True)[:50]


def make_excerpt(text: str, query_tokens: list[str], expanded: set[str]) -> str:
    lower = text.lower()
    first_position = None
    for token in query_tokens + list(expanded):
        position = lower.find(token.lower())
        if position >= 0 and (first_position is None or position < first_position):
            first_position = position
    if first_position is None:
        first_position = 0
    start = max(first_position - 90, 0)
    end = min(first_position + 220, len(text))
    excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
    if start > 0:
        excerpt = f"... {excerpt}"
    if end < len(text):
        excerpt = f"{excerpt} ..."
    return excerpt


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left[token] * right.get(token, 0) for token in left)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / max(norm_left * norm_right, 1e-9)


def _matches_filters(doc: dict[str, Any], params: dict[str, str]) -> bool:
    for field in ("client_id", "matter_id", "type", "responsible"):
        value = params.get(field)
        if value and doc.get(field) != value:
            return False
    date_from = params.get("from")
    date_to = params.get("to")
    document_date = doc.get("document_date") or ""
    if date_from and document_date and document_date < date_from:
        return False
    if date_to and document_date and document_date > date_to:
        return False
    return True


def _can_access_doc(doc: dict[str, Any], allowed_matter_ids: set[str], allowed_client_ids: set[str]) -> bool:
    matter_id = doc.get("matter_id")
    client_id = doc.get("client_id")
    return matter_id in allowed_matter_ids or client_id in allowed_client_ids or (not matter_id and not client_id)
