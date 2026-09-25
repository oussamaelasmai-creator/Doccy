from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from .search import tokenize


MAX_ANALYSIS_TEXT = 15_000
MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}
DOC_TYPE_KEYWORDS = {
    "Certificat medical": {
        "certificat",
        "medical",
        "patiente",
        "examen",
        "plaie",
        "incapacite",
        "urgence",
        "antitetanique",
        "rabique",
    },
    "Facture": {"facture", "montant", "total", "tva", "paiement", "echeance", "fournisseur"},
    "Jugement": {"jugement", "tribunal", "ordonne", "condamne", "audience", "greffe", "appel"},
    "Attestation": {"atteste", "attestation", "honneur", "certifie", "temoignage"},
    "Contrat": {"contrat", "clause", "parties", "obligation", "signature", "accord"},
    "Courrier": {"madame", "monsieur", "objet", "salutations", "courrier", "lettre"},
    "Piece d'identite": {"passeport", "identite", "republique", "nationalite", "naissance"},
}
TAG_KEYWORDS = {
    "morsure": {"morsure", "chien", "animal", "crocs", "carnivore", "mordeur", "plaie"},
    "medical": {"medical", "patiente", "urgence", "traitement", "incapacite"},
    "recouvrement": {"recouvrement", "creance", "impaye", "facture", "relance"},
    "contrat": {"contrat", "clause", "signature", "obligation"},
    "jugement": {"jugement", "tribunal", "audience", "greffe"},
}
MATTER_HINTS = {
    "chien": {"chien", "animal", "carnivore", "crocs", "morsure", "mordeur", "plaie", "attaque", "agression"},
    "morsure": {"chien", "animal", "carnivore", "crocs", "mordeur", "plaie", "attaque", "agression"},
    "recouvrement": {"facture", "creance", "impaye", "paiement", "relance"},
    "fournisseur": {"facture", "creance", "paiement", "commande"},
}


@dataclass
class DocumentAnalysis:
    title: str
    doc_type: str
    doc_date: str
    persons: list[str]
    summary: str
    tags: list[str]
    matter_id: str | None
    method: str
    confidence: float


def analysis_status() -> dict[str, Any]:
    provider = os.environ.get("DOCUMENT_ANALYSIS_PROVIDER", "rules").lower()
    return {
        "provider": provider,
        "ollama_available": _ollama_available(),
        "ollama_model": os.environ.get("DOCUMENT_ANALYSIS_MODEL", "mistral-small3.2"),
    }


def analyze_document(store: dict[str, Any], document: dict[str, Any], pages: list[dict[str, Any]], keep_matter: bool = False) -> DocumentAnalysis:
    text = _document_text(pages)
    ollama = _analyze_with_ollama(store, document, text, keep_matter)
    rules = _analyze_with_rules(store, document, text, keep_matter)
    if ollama:
        return _merge_analysis(ollama, rules)
    return rules


def apply_analysis(document: dict[str, Any], analysis: DocumentAnalysis, keep_matter: bool = False) -> None:
    if analysis.title:
        document["title"] = analysis.title
    if analysis.doc_type:
        document["type"] = analysis.doc_type
        document["doc_type"] = analysis.doc_type
    if analysis.doc_date and not document.get("document_date"):
        document["document_date"] = analysis.doc_date
    document["persons"] = analysis.persons
    document["summary"] = analysis.summary
    document["tags"] = analysis.tags
    document["analysis_method"] = analysis.method
    document["analysis_confidence"] = analysis.confidence
    if analysis.matter_id and not keep_matter:
        document["matter_id"] = analysis.matter_id


def _merge_analysis(ollama: DocumentAnalysis, rules: DocumentAnalysis) -> DocumentAnalysis:
    return DocumentAnalysis(
        title=ollama.title or rules.title,
        doc_type=ollama.doc_type or rules.doc_type,
        doc_date=ollama.doc_date or rules.doc_date,
        persons=ollama.persons or rules.persons,
        summary=ollama.summary or rules.summary,
        tags=ollama.tags or rules.tags,
        matter_id=ollama.matter_id or rules.matter_id,
        method=ollama.method,
        confidence=max(ollama.confidence, rules.confidence),
    )


def _analyze_with_rules(store: dict[str, Any], document: dict[str, Any], text: str, keep_matter: bool) -> DocumentAnalysis:
    tokens = set(tokenize(text))
    doc_type = _detect_doc_type(tokens) or document.get("type") or "Document"
    doc_date = _extract_date(text) or document.get("document_date") or ""
    persons = _extract_persons(text)
    tags = _extract_tags(tokens, doc_type)
    matter_id, matter_score = (document.get("matter_id"), 1.0) if keep_matter else _classify_matter(store, text, tokens, tags)
    title = _build_title(document, text, doc_type, persons, doc_date)
    summary = _build_summary(text, doc_type, persons, doc_date, tags)
    confidence = round(0.52 + min(len(tokens) / 180, 0.22) + min(matter_score, 0.16), 3)
    return DocumentAnalysis(title, doc_type, doc_date, persons, summary, tags, matter_id, "local-rules", min(confidence, 0.92))


def _analyze_with_ollama(store: dict[str, Any], document: dict[str, Any], text: str, keep_matter: bool) -> DocumentAnalysis | None:
    provider = os.environ.get("DOCUMENT_ANALYSIS_PROVIDER", "rules").lower()
    if provider not in {"auto", "ollama"} or not text.strip():
        return None
    payload = {
        "model": os.environ.get("DOCUMENT_ANALYSIS_MODEL", "mistral-small3.2"),
        "stream": False,
        "format": "json",
        "prompt": _ollama_prompt(store, document, text, keep_matter),
        "options": {"temperature": 0.1},
    }
    try:
        request = urllib.request.Request(
            os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        data = json.loads(raw.get("response") or "{}")
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return _analysis_from_payload(store, data, method=f"ollama:{payload['model']}")


def _ollama_prompt(store: dict[str, Any], document: dict[str, Any], text: str, keep_matter: bool) -> str:
    matters = "\n".join(
        f"- id {matter['id']}: {matter.get('name', '')} — responsable {matter.get('responsible', '')}"
        for matter in store.get("matters", [])
    ) or "(aucune affaire)"
    clipped = _clip_text(text)
    forced = f"\nAffaire imposee: {document.get('matter_id')}" if keep_matter and document.get("matter_id") else ""
    return f"""Tu analyses localement un document de cabinet. Le texte OCR peut contenir des erreurs.
Réponds uniquement avec un JSON valide contenant:
title, doc_type, doc_date au format YYYY-MM-DD ou "", persons liste, summary 3 phrases max, tags liste, matter_id ou null.
N'invente pas de personne, de date ou d'affaire. matter_id doit faire partie des affaires listées.

Affaires disponibles:
{matters}{forced}

Texte OCR:
\"\"\"
{clipped}
\"\"\"
"""


def _analysis_from_payload(store: dict[str, Any], data: dict[str, Any], method: str) -> DocumentAnalysis:
    valid_matter_ids = {matter["id"] for matter in store.get("matters", [])}
    matter_id = data.get("matter_id")
    if matter_id not in valid_matter_ids:
        matter_id = None
    return DocumentAnalysis(
        title=_string(data.get("title"))[:140],
        doc_type=_string(data.get("doc_type"))[:80],
        doc_date=_date_or_empty(data.get("doc_date")),
        persons=_string_list(data.get("persons"), 12),
        summary=_string(data.get("summary"))[:900],
        tags=_string_list(data.get("tags"), 8),
        matter_id=matter_id,
        method=method,
        confidence=0.9,
    )


def _document_text(pages: list[dict[str, Any]]) -> str:
    if len(pages) <= 1:
        return pages[0].get("text", "") if pages else ""
    chunks = []
    for page in sorted(pages, key=lambda item: item.get("page_number", 0)):
        chunks.append(f"--- Page {page.get('page_number')} ---\n{page.get('text', '')}")
    return "\n\n".join(chunks)


def _detect_doc_type(tokens: set[str]) -> str:
    scored = []
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        hits = len(tokens & keywords)
        if hits:
            scored.append((hits, doc_type))
    return max(scored, default=(0, ""))[1]


def _extract_date(text: str) -> str:
    numeric = re.search(r"\b([0-3]?\d)[/-]([01]?\d)[/-]((?:19|20)\d{2})\b", text)
    if numeric:
        return _safe_date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    french = re.search(r"\b([0-3]?\d)\s+([A-Za-zÀ-ÿ]+)\s+((?:19|20)\d{2})\b", text, re.IGNORECASE)
    if french:
        month = MONTHS.get(french.group(2).lower())
        if month:
            return _safe_date(int(french.group(3)), month, int(french.group(1)))
    iso = re.search(r"\b((?:19|20)\d{2})-([01]\d)-([0-3]\d)\b", text)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    return ""


def _safe_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _extract_persons(text: str) -> list[str]:
    candidates = []
    for pattern in (
        r"\bClient\s+([A-ZÉÈÀÂÊÎÔÛÇ][A-Za-zÀ-ÿ'-]+\s+[A-Z][A-ZÀ-Ÿ'-]{2,})\b",
        r"\b(?:Monsieur|Madame|Patient|Patiente)\s+([A-ZÉÈÀÂÊÎÔÛÇ][A-Za-zÀ-ÿ'-]+\s+[A-Z][A-ZÀ-Ÿ'-]{2,})\b",
        r"\bRedacteur\s+((?:Dr|Docteur|Maître|Maitre)\s+[A-ZÉÈÀÂÊÎÔÛÇ][A-Za-zÀ-ÿ'-]+\s+[A-Z][A-ZÀ-Ÿ'-]{2,})\b",
    ):
        candidates.extend(re.findall(pattern, text))
    candidates.extend(re.findall(r"\b(?:Dr|Docteur|Maître|Maitre)\s+[A-ZÉÈÀÂÊÎÔÛÇ][A-Za-zÀ-ÿ'-]+\s+[A-Z][A-ZÀ-Ÿ'-]{2,}\b", text))
    candidates.extend(re.findall(r"\b[A-ZÉÈÀÂÊÎÔÛÇ][A-Za-zÀ-ÿ'-]+\s+[A-Z][A-ZÀ-Ÿ'-]{2,}\b", text))
    cleaned = []
    seen_keys = set()
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate.replace(" - ", " ")).strip(" .,:;-")
        if len(value) < 4 or value.lower() in {"service des urgences"} or value.startswith("Affaire "):
            continue
        key = re.sub(r"^(?:dr|docteur|maître|maitre)\s+", "", value.lower())
        if key not in seen_keys:
            cleaned.append(value)
            seen_keys.add(key)
    return cleaned[:10]


def _extract_tags(tokens: set[str], doc_type: str) -> list[str]:
    tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if tokens & keywords:
            tags.append(tag)
    if doc_type and doc_type.lower() not in {tag.lower() for tag in tags}:
        tags.insert(0, doc_type.lower())
    return tags[:6]


def _classify_matter(store: dict[str, Any], text: str, tokens: set[str], tags: list[str]) -> tuple[str | None, float]:
    best_id = None
    best_score = 0.0
    for matter in store.get("matters", []):
        matter_tokens = set(tokenize(" ".join([matter.get("name", ""), matter.get("responsible", "")])))
        expanded = set(matter_tokens)
        for token in list(matter_tokens):
            expanded.update(MATTER_HINTS.get(token, set()))
        score = len((tokens | set(tags)) & expanded) / max(len(expanded), 1)
        phrase = matter.get("name", "").lower()
        if phrase and phrase in text.lower():
            score += 0.35
        if score > best_score:
            best_id = matter["id"]
            best_score = score
    if best_score >= 0.08:
        return best_id, best_score
    return None, 0.0


def _build_title(document: dict[str, Any], text: str, doc_type: str, persons: list[str], doc_date: str) -> str:
    base = doc_type or "Document"
    if persons:
        base = f"{base} - {persons[0]}"
    if doc_date:
        base = f"{base} ({doc_date})"
    if base != "Document":
        return base[:140]
    for line in re.split(r"[\n.]", text):
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) > 3:
            return line[:137] + ("..." if len(line) > 137 else "")
    return document.get("filename") or "Document"


def _build_summary(text: str, doc_type: str, persons: list[str], doc_date: str, tags: list[str]) -> str:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if len(sentence.strip()) > 35]
    key = sentences[:2]
    intro = f"Document analyse comme {doc_type or 'document'}"
    if doc_date:
        intro += f" date du {doc_date}"
    if persons:
        intro += f", concernant {', '.join(persons[:3])}"
    intro += "."
    if tags:
        intro += f" Themes detectes: {', '.join(tags[:5])}."
    return " ".join([intro, *key])[:900]


def _clip_text(text: str) -> str:
    if len(text) <= MAX_ANALYSIS_TEXT:
        return text
    return f"{text[:12_000]}\n[...]\n{text[-3_000:]}"


def _ollama_available() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.5) as response:
            return response.status == 200
    except OSError:
        return False


def _string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _string(item)
        if text and text not in result:
            result.append(text[:120])
    return result[:limit]


def _date_or_empty(value: Any) -> str:
    text = _string(value)
    return text if re.fullmatch(r"(?:19|20)\d{2}-[01]\d-[0-3]\d", text) else ""
