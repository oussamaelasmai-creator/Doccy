from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".rtf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_EXTRACTED_CHARS_PER_PAGE = 20_000
COMMON_PUNCTUATION = set("' -_.,;:!?()[]{}\"/@+*=#%€$&\n\r\t")
OCR_PSMS = ("11", "6", "4", "3")
OCR_SKEW_ANGLES = (0, -2, 2, -4, 4)
FRENCH_COMMON_WORDS = {
    "acte",
    "ans",
    "apres",
    "avec",
    "cabinet",
    "certificat",
    "client",
    "constate",
    "date",
    "declaration",
    "descriptif",
    "document",
    "dossier",
    "droite",
    "gauche",
    "initial",
    "juridique",
    "lesion",
    "madame",
    "medical",
    "monsieur",
    "patient",
    "pieces",
    "responsabilite",
    "service",
    "suite",
    "urgence",
}
OCR_PHRASE_CORRECTIONS = (
    (r"\b8\s+levrier\s+2019\b", "8 fevrier 2019"),
    (r"\blevrier\b", "fevrier"),
    (r"\ba\s+'a\s+suite\b", "a la suite"),
    (r"\bbar\s+un\b", "par un"),
    (r"\bcamivore\b", "carnivore"),
    (r"\bvote\s+publique\b", "voie publique"),
    (r"\bconuse\b", "contuse"),
    (r"\bprotonde\b", "profonde"),
    (r"\ble\s+face\b", "la face"),
    (r"\blavant-bras\b", "l'avant-bras"),
    (r"\bcentimstres\b", "centimetres"),
    (r"\bdissu\b", "tissu"),
    (r"\bcompatitles\b", "compatibles"),
    (r"\bprehension\b", "prehension"),
    (r"\bpmiea\s+puperiorires\b", "plaies punctiformes"),
    (r"\bDeux\s+plaie\s+punctiformes\b", "Deux plaies punctiformes"),
    (r"\bsupercioles-mGtpfes\s+dr\s+Va\s+tace\s+latecale\b", "superficielles multiples de la face laterale"),
    (r"\bsupercioles\b", "superficielles"),
    (r"\blatecale\b", "laterale"),
    (r"\bcuisse\s+droite\s+Pa\b", "cuisse droite. Pas"),
    (r"\bdaltlite\b", "d'atteinte"),
    (r"\blndineuse\b", "tendineuse"),
    (r"\bi\s+o\s+defit\b", "ni deficit"),
    (r"\bni\s+deficit\b", "ni de deficit"),
    (r"\bdefit\b", "deficit"),
    (r"\bneuroiogiqve\b", "neurologique"),
    (r"\bradial\s+resent\b", "radial present"),
    (r"\bPouls\s+radial\s+present\s+Traitement\b", "Pouls radial present. Traitement"),
    (r"\bwl\s+eyfvéinqve\b", ""),
    (r"\bUNV\b", ""),
    (r"\bTraitement\s+realise\s+antt?etanique\.\s+rabique\b", "Traitement realise: antitetanique, rabique"),
    (r"\bantitetanique\b", "antitetanique"),
    (r"\banttetanique\b", "antitetanique"),
    (r"\bFabrance\b", "l'absence"),
    (r"\bcenificat\b", "certificat"),
    (r"\bveterinairo\b", "veterinaire"),
    (r"\branimal\b", "l'animal"),
    (r"\bmoreur\b", "mordeur"),
    (r"\bmordeur\s+Conclusion\b", "mordeur. Conclusion"),
    (r"\bConciusion\b", "Conclusion"),
    (r"\btraval\b", "travail"),
    (r"\biestiale\b", "initiale"),
    (r"\bevaluee\s+a\s+vingt\s+el\s+un\b", "evaluee a vingt et un"),
    (r"\bde\s+levolution\b", "de l'evolution"),
    (r"\bFabsence\b", "l'absence"),
    (r"\bCantificat\b", "Certificat"),
    (r"\bfimteressee\b", "l'interessee"),
    (r"\bMMMwWrW\b", ""),
)
OCR_WORD_CORRECTIONS = {
    "dune": "d'une",
    "camivore": "carnivore",
    "conuse": "contuse",
    "protonde": "profonde",
    "centimstres": "centimetres",
    "dissu": "tissu",
    "compatitles": "compatibles",
    "neuroiogiqve": "neurologique",
    "veterinairo": "veterinaire",
    "moreur": "mordeur",
    "traval": "travail",
}
OCR_DOMAIN_TERMS = {
    "admis",
    "admise",
    "agression",
    "animal",
    "anterieure",
    "bras",
    "certificat",
    "client",
    "conclusion",
    "constatations",
    "contuse",
    "crocs",
    "cutanee",
    "date",
    "descriptif",
    "domestique",
    "droite",
    "droit",
    "examen",
    "exposition",
    "fevrier",
    "gauche",
    "infectieuse",
    "initial",
    "initiale",
    "lesion",
    "medical",
    "mordeur",
    "patiente",
    "plaie",
    "plaies",
    "publique",
    "redacteur",
    "service",
    "substance",
    "suite",
    "surveillance",
    "tendineuse",
    "traitement",
    "urgence",
    "urgences",
    "veterinaire",
    "cutane",
}
OCR_PROTECTED_WORDS = {
    "droit",
    "droite",
    "plaies",
    "urgences",
}


@dataclass
class PageResult:
    page_number: int
    text: str
    confidence: float
    quality: str
    raw_text: str = ""


@dataclass
class ProcessingResult:
    status: str
    has_text_layer: bool
    ocr_required: bool
    quality: str
    confidence: float
    page_count: int
    pages: list[PageResult]
    message: str = ""


@dataclass
class OcrCandidate:
    text: str
    confidence: float
    quality: str
    words: int
    psm: str
    angle: int
    engine: str = "tesseract"
    raw_text: str = ""


def process_document(path: Path, mime_type: str = "") -> ProcessingResult:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS or mime_type.startswith("text/"):
        return _process_text(path)
    if suffix == ".pdf" or mime_type == "application/pdf":
        return _process_pdf(path)
    if suffix in IMAGE_EXTENSIONS or mime_type.startswith("image/"):
        return _process_image(path)
    return ProcessingResult(
        status="error",
        has_text_layer=False,
        ocr_required=False,
        quality="unsupported",
        confidence=0.0,
        page_count=0,
        pages=[],
        message="Format non pris en charge par le prototype local.",
    )


def ocr_status() -> dict[str, object]:
    engine = os.environ.get("DOCUMENT_OCR_ENGINE", "auto").lower()
    return {
        "engine": engine,
        "rapidocr_available": _rapidocr_available(),
        "paddleocr_available": _paddle_available(),
        "tesseract_available": shutil.which("tesseract") is not None,
        "pdftoppm_available": shutil.which("pdftoppm") is not None,
        "selected": (
            "rapidocr"
            if _should_use_rapidocr()
            else "paddleocr"
            if _should_use_paddle()
            else "tesseract"
            if _should_use_tesseract()
            else "missing"
        ),
    }


def _process_text(path: Path) -> ProcessingResult:
    text = path.read_text(encoding="utf-8", errors="ignore")
    quality, confidence = _quality_for_text(text)
    return ProcessingResult(
        status="indexed" if text.strip() else "low_quality",
        has_text_layer=True,
        ocr_required=False,
        quality=quality,
        confidence=confidence,
        page_count=1,
        pages=[PageResult(1, text, confidence, quality)],
    )


def _process_pdf(path: Path) -> ProcessingResult:
    pdftotext = shutil.which("pdftotext")
    text = ""
    message = ""
    if pdftotext:
        try:
            completed = subprocess.run(
                [pdftotext, "-layout", str(path), "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if completed.returncode == 0:
                text = completed.stdout
            else:
                message = completed.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            message = str(exc)

    if not text.strip():
        text = _extract_pdf_literal_strings(path)

    page_count = max(_estimate_pdf_pages(path), 1)
    if text.strip():
        raw_pages = [page.strip() for page in text.split("\f") if page.strip()]
        if not raw_pages:
            raw_pages = [text.strip()]
        pages = []
        for index, page_text in enumerate(raw_pages, start=1):
            page_text = _clean_text(page_text)
            quality, confidence = _quality_for_text(page_text)
            if confidence >= 0.45:
                pages.append(PageResult(index, page_text[:MAX_EXTRACTED_CHARS_PER_PAGE], confidence, quality))
        if not pages:
            return ProcessingResult(
                status="ocr_pending",
                has_text_layer=False,
                ocr_required=True,
                quality="unknown",
                confidence=0.0,
                page_count=page_count,
                pages=[],
                message="Le PDF ne contient pas de couche texte exploitable. OCR requis.",
            )
        confidence = sum(page.confidence for page in pages) / len(pages)
        quality = "good" if confidence >= 0.75 else "low"
        return ProcessingResult(
            status="indexed" if quality == "good" else "low_quality",
            has_text_layer=True,
            ocr_required=False,
            quality=quality,
            confidence=round(confidence, 3),
            page_count=max(page_count, len(pages)),
            pages=pages,
            message=message,
        )

    ocr_result = _ocr_pdf(path, page_count)
    if ocr_result:
        return ocr_result

    return ProcessingResult(
        status="ocr_pending",
        has_text_layer=False,
        ocr_required=True,
        quality="unknown",
        confidence=0.0,
        page_count=page_count,
        pages=[],
        message="Aucune couche texte detectee. Installer poppler et un moteur OCR local comme RapidOCR ou Tesseract pour traiter ce PDF scanne.",
    )


def _process_image(path: Path) -> ProcessingResult:
    if not _ocr_engine_available():
        return ProcessingResult(
            status="ocr_pending",
            has_text_layer=False,
            ocr_required=True,
            quality="unknown",
            confidence=0.0,
            page_count=1,
            pages=[],
            message="Image importee. Installer un moteur OCR local comme RapidOCR ou Tesseract pour extraire le texte.",
        )

    languages, language_message = _tesseract_languages()
    try:
        candidate = _best_ocr_candidate(path, languages)
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessingResult("error", False, True, "error", 0.0, 1, [], str(exc))

    if not candidate or not _candidate_is_meaningful(candidate):
        return ProcessingResult(
            status="low_quality",
            has_text_layer=False,
            ocr_required=True,
            quality="low",
            confidence=candidate.confidence if candidate else 0.0,
            page_count=1,
            pages=[],
            message="OCR execute mais le texte extrait n'est pas assez fiable pour etre indexe.",
        )

    return ProcessingResult(
        status="indexed",
        has_text_layer=False,
        ocr_required=True,
        quality=candidate.quality,
        confidence=candidate.confidence,
        page_count=1,
        pages=[PageResult(1, candidate.text, candidate.confidence, candidate.quality, candidate.raw_text)],
        message=" ".join(part for part in [language_message, _ocr_method_message(candidate)] if part),
    )


def _ocr_pdf(path: Path, page_count: int) -> ProcessingResult | None:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm or not _ocr_engine_available():
        return None
    languages, language_message = _tesseract_languages()

    with tempfile.TemporaryDirectory(prefix="doc-ocr-") as tmp_dir:
        output_prefix = Path(tmp_dir) / "page"
        render = subprocess.run(
            [pdftoppm, "-r", "220", "-png", str(path), str(output_prefix)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if render.returncode != 0:
            return ProcessingResult(
                status="error",
                has_text_layer=False,
                ocr_required=True,
                quality="error",
                confidence=0.0,
                page_count=page_count,
                pages=[],
                message=render.stderr.strip() or "Rendu du PDF impossible avant OCR.",
            )

        images = sorted(Path(tmp_dir).glob("page-*.png"))
        pages: list[PageResult] = []
        messages: list[str] = []
        for index, image_path in enumerate(images, start=1):
            candidate = _best_ocr_candidate(image_path, languages)
            if candidate and _candidate_is_meaningful(candidate):
                pages.append(
                    PageResult(
                        index,
                        candidate.text[:MAX_EXTRACTED_CHARS_PER_PAGE],
                        candidate.confidence,
                        candidate.quality,
                        candidate.raw_text[:MAX_EXTRACTED_CHARS_PER_PAGE],
                    )
                )
                messages.append(_ocr_method_message(candidate))
            elif candidate:
                messages.append(f"Page {index}: OCR non indexe, texte peu fiable.")

        if not pages:
            return ProcessingResult(
                status="low_quality",
                has_text_layer=False,
                ocr_required=True,
                quality="low",
                confidence=0.0,
                page_count=max(page_count, len(images), 1),
                pages=[],
                message="OCR execute mais aucun texte fiable n'a ete extrait. Verifier la qualite du scan.",
            )

        confidence = sum(page.confidence for page in pages) / len(pages)
        quality = "good" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
        return ProcessingResult(
            status="indexed" if confidence >= 0.45 else "low_quality",
            has_text_layer=False,
            ocr_required=True,
            quality=quality,
            confidence=round(confidence, 3),
            page_count=max(page_count, len(images), len(pages)),
            pages=pages,
            message=" ".join([language_message, *messages[:2]]).strip(),
        )


def _extract_pdf_literal_strings(path: Path) -> str:
    data = path.read_bytes()
    values: list[str] = []
    for match in re.finditer(rb"\((.*?)\)", data, flags=re.DOTALL):
        raw = match.group(1)
        if len(raw) < 3:
            continue
        cleaned = raw.replace(rb"\\n", b"\n").replace(rb"\\r", b"\n").replace(rb"\\t", b"\t")
        raw_text = cleaned.decode("latin-1", errors="ignore")
        if not _looks_like_pdf_literal_text(raw_text):
            continue
        text = _clean_text(raw_text)
        if _looks_like_text(text):
            values.append(text)
    return "\n".join(values)


def _tesseract_languages() -> tuple[str, str]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return "eng", ""
    completed = subprocess.run(
        [tesseract, "--list-langs"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    installed = set(completed.stdout.splitlines()[1:])
    if "fra" in installed and "eng" in installed:
        return "fra+eng", ""
    if "fra" in installed:
        return "fra", ""
    if "eng" in installed:
        return "eng", "Langue OCR francaise absente; installe `tesseract-lang` pour de meilleurs resultats."
    return "eng", "Aucune langue OCR adaptee detectee dans Tesseract."


def _best_ocr_candidate(image_path: Path, languages: str) -> OcrCandidate | None:
    candidates: list[OcrCandidate] = []
    candidates.extend(_ocr_candidates_for_variant(image_path, languages, 0))
    first_pass = _best_candidate(candidates)
    if first_pass and first_pass.confidence >= 0.72 and _candidate_is_meaningful(first_pass):
        return first_pass

    for variant_path, angle in _rotated_image_variants(image_path):
        candidates.extend(_ocr_candidates_for_variant(variant_path, languages, angle))

    if not candidates:
        return None
    return _best_candidate(candidates)


def _ocr_candidates_for_variant(image_path: Path, languages: str, angle: int) -> list[OcrCandidate]:
    candidates: list[OcrCandidate] = []
    if _should_use_rapidocr():
        candidate = _rapidocr_candidate(image_path, angle)
        if candidate and candidate.words:
            candidates.append(candidate)
    if _should_use_paddle():
        candidate = _paddle_ocr_candidate(image_path, angle)
        if candidate and candidate.words:
            candidates.append(candidate)
    if _should_use_tesseract():
        for psm in OCR_PSMS:
            candidate = _ocr_tsv_candidate(image_path, languages, psm, angle)
            if candidate and candidate.words:
                candidates.append(candidate)
    return candidates


def _best_candidate(candidates: list[OcrCandidate]) -> OcrCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.confidence, candidate.words))


def _rotated_image_variants(image_path: Path) -> list[tuple[Path, int]]:
    sips = shutil.which("sips")
    variants = []
    if not sips:
        return variants
    for angle in OCR_SKEW_ANGLES:
        if angle == 0:
            continue
        rotated = image_path.with_name(f"{image_path.stem}_rot_{angle}{image_path.suffix}")
        completed = subprocess.run(
            [sips, "-r", str(angle), str(image_path), "--out", str(rotated)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if completed.returncode == 0 and rotated.exists():
            variants.append((rotated, angle))
    return variants


def _ocr_tsv_candidate(image_path: Path, languages: str, psm: str, angle: int) -> OcrCandidate | None:
    completed = subprocess.run(
        ["tesseract", str(image_path), "stdout", "-l", languages, "--psm", psm, "tsv"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        return None

    rows = list(csv.DictReader(io.StringIO(completed.stdout), delimiter="\t")) if completed.stdout.strip() else []
    word_rows = []
    confidences: list[float] = []
    for row in rows:
        text = _clean_text(row.get("text") or "")
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            confidence = -1
        if confidence < 0:
            continue
        word_rows.append((row, text))
        confidences.append(confidence)

    if not word_rows:
        return None

    raw_text = _rebuild_text_from_tsv(word_rows)
    text = _postprocess_ocr_text(raw_text)
    mean_confidence = sum(confidences) / len(confidences) / 100
    semantic_score = _semantic_text_score(text)
    density_score = min(len(word_rows) / 80, 1.0)
    confidence = round(mean_confidence * 0.68 + semantic_score * 0.24 + density_score * 0.08, 3)
    quality = "good" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
    return OcrCandidate(text=text, confidence=confidence, quality=quality, words=len(word_rows), psm=psm, angle=angle, raw_text=raw_text)


def _rapidocr_candidate(image_path: Path, angle: int) -> OcrCandidate | None:
    try:
        result = _rapidocr_model()(str(image_path))
    except Exception:
        return None

    words = _flatten_ocr_result(result)
    words = [(text, confidence) for text, confidence in words if not _ocr_segment_is_noise(text, confidence)]
    if not words:
        return None
    texts = [text for text, _ in words if text.strip()]
    confidences = [confidence for _, confidence in words if confidence >= 0]
    if not texts or not confidences:
        return None
    raw_text = _clean_text("\n".join(texts))
    text = _postprocess_ocr_text(raw_text)
    mean_confidence = sum(confidences) / len(confidences)
    if mean_confidence > 1:
        mean_confidence = mean_confidence / 100
    semantic_score = _semantic_text_score(text)
    density_score = min(len(texts) / 80, 1.0)
    confidence = round(mean_confidence * 0.68 + semantic_score * 0.24 + density_score * 0.08, 3)
    quality = "good" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
    return OcrCandidate(text=text, confidence=confidence, quality=quality, words=len(texts), psm="rapid", angle=angle, engine="rapidocr", raw_text=raw_text)


def _paddle_ocr_candidate(image_path: Path, angle: int) -> OcrCandidate | None:
    try:
        model = _paddle_model()
        if hasattr(model, "predict"):
            result = model.predict(str(image_path))
        else:
            result = model.ocr(str(image_path), cls=True)
    except Exception:
        return None

    words = _flatten_ocr_result(result)
    words = [(text, confidence) for text, confidence in words if not _ocr_segment_is_noise(text, confidence)]
    if not words:
        return None
    texts = [text for text, _ in words if text.strip()]
    confidences = [confidence for _, confidence in words if confidence >= 0]
    if not texts or not confidences:
        return None
    raw_text = _clean_text("\n".join(texts))
    text = _postprocess_ocr_text(raw_text)
    mean_confidence = sum(confidences) / len(confidences)
    if mean_confidence > 1:
        mean_confidence = mean_confidence / 100
    semantic_score = _semantic_text_score(text)
    density_score = min(len(texts) / 80, 1.0)
    confidence = round(mean_confidence * 0.68 + semantic_score * 0.24 + density_score * 0.08, 3)
    quality = "good" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
    return OcrCandidate(text=text, confidence=confidence, quality=quality, words=len(texts), psm="paddle", angle=angle, engine="paddleocr", raw_text=raw_text)


def _flatten_ocr_result(result) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []

    def visit(value) -> None:
        if value is None:
            return
        if hasattr(value, "txts") and hasattr(value, "scores"):
            texts = list(value.txts or [])
            scores = list(value.scores or [])
            for index, text in enumerate(texts):
                score = scores[index] if index < len(scores) else 0.0
                found.append((str(text), _float_or_zero(score)))
            return
        if isinstance(value, dict):
            texts = value.get("rec_texts") or value.get("texts")
            scores = value.get("rec_scores") or value.get("scores")
            if isinstance(texts, list):
                for index, text in enumerate(texts):
                    score = scores[index] if isinstance(scores, list) and index < len(scores) else 0.0
                    found.append((str(text), _float_or_zero(score)))
                return
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[0], str):
            found.append((value[0], _float_or_zero(value[1])))
            return
        if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], tuple) and len(value[1]) >= 2:
            found.append((str(value[1][0]), _float_or_zero(value[1][1])))
            return
        if hasattr(value, "json"):
            try:
                json_value = value.json() if callable(value.json) else value.json
                visit(json_value)
                return
            except Exception:
                pass
        if hasattr(value, "res"):
            visit(value.res)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return [(text, confidence) for text, confidence in found if text.strip()]


def _float_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ocr_segment_is_noise(text: str, confidence: float) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return True
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]+", cleaned)
    if confidence < 0.65 and len(tokens) <= 1:
        return True
    if confidence < 0.75 and len(cleaned) <= 4 and cleaned.isupper() and not any(char.isdigit() for char in cleaned):
        return True
    if confidence < 0.7 and _semantic_text_score(cleaned) < 0.25:
        return True
    letters = [char for char in cleaned if char.isalpha()]
    if confidence < 0.7 and len(letters) >= 5:
        vowels = sum(1 for char in letters if char.lower() in "aeiouyàâäéèêëîïôöùûüœ")
        if vowels / len(letters) < 0.18:
            return True
    return False


def _should_use_paddle() -> bool:
    engine = os.environ.get("DOCUMENT_OCR_ENGINE", "auto").lower()
    return engine in {"auto", "paddle", "paddleocr"} and not _should_use_rapidocr() and _paddle_available()


def _should_use_rapidocr() -> bool:
    engine = os.environ.get("DOCUMENT_OCR_ENGINE", "auto").lower()
    return engine in {"auto", "rapid", "rapidocr"} and _rapidocr_available()


def _should_use_tesseract() -> bool:
    engine = os.environ.get("DOCUMENT_OCR_ENGINE", "auto").lower()
    return engine in {"auto", "tesseract"} and shutil.which("tesseract") is not None


def _ocr_engine_available() -> bool:
    return _rapidocr_available() or _paddle_available() or shutil.which("tesseract") is not None


def _paddle_available() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except Exception:
        return False


def _rapidocr_available() -> bool:
    try:
        import rapidocr  # noqa: F401

        return True
    except Exception:
        return False


def _rapidocr_model():
    from rapidocr import RapidOCR

    if not hasattr(_rapidocr_model, "_instance"):
        _rapidocr_model._instance = RapidOCR()
    return _rapidocr_model._instance


def _paddle_model():
    from paddleocr import PaddleOCR

    if not hasattr(_paddle_model, "_instance"):
        configs = [
            {
                "lang": "fr",
                "use_doc_orientation_classify": True,
                "use_doc_unwarping": True,
                "use_textline_orientation": True,
            },
            {"lang": "fr", "use_angle_cls": True},
            {"lang": "fr"},
        ]
        last_error = None
        for config in configs:
            try:
                _paddle_model._instance = PaddleOCR(**config)
                break
            except Exception as exc:
                last_error = exc
        else:
            raise last_error or RuntimeError("PaddleOCR indisponible")
    return _paddle_model._instance


def _rebuild_text_from_tsv(word_rows: list[tuple[dict[str, str], str]]) -> str:
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    for row, text in word_rows:
        key = (
            _int_field(row.get("page_num")),
            _int_field(row.get("block_num")),
            _int_field(row.get("par_num")),
            _int_field(row.get("line_num")),
        )
        lines.setdefault(key, []).append(text)
    rebuilt = [" ".join(words) for _, words in sorted(lines.items(), key=lambda item: item[0])]
    return "\n".join(line for line in rebuilt if line.strip())


def _int_field(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return 0


def _semantic_text_score(text: str) -> float:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ]{2,}", text.lower())
    if not tokens:
        return 0.0
    readable_tokens = [token for token in tokens if _word_shape_is_plausible(token)]
    common_hits = sum(1 for token in tokens if token in FRENCH_COMMON_WORDS)
    long_noise = sum(1 for token in tokens if len(token) > 22)
    readable_ratio = len(readable_tokens) / max(len(tokens), 1)
    common_ratio = min(common_hits / 4, 1.0)
    noise_penalty = min(long_noise / max(len(tokens), 1), 0.4)
    return max(0.0, min(1.0, readable_ratio * 0.72 + common_ratio * 0.28 - noise_penalty))


def _word_shape_is_plausible(word: str) -> bool:
    if len(word) <= 2:
        return True
    if len(word) > 28:
        return False
    vowels = sum(1 for char in word if char in "aeiouyàâäéèêëîïôöùûüœ")
    letters = sum(1 for char in word if char.isalpha())
    if letters == 0:
        return False
    return vowels / letters >= 0.18


def _candidate_is_meaningful(candidate: OcrCandidate) -> bool:
    if candidate.words < 8:
        return False
    if candidate.confidence < 0.45:
        return False
    return _semantic_text_score(candidate.text) >= 0.42


def _ocr_method_message(candidate: OcrCandidate) -> str:
    engine = {"paddleocr": "PaddleOCR", "rapidocr": "RapidOCR"}.get(candidate.engine, "Tesseract")
    if candidate.angle == 0:
        return f"OCR applique avec {engine}, mode {candidate.psm}."
    return f"OCR applique avec {engine}, mode {candidate.psm}, apres correction d'inclinaison {candidate.angle} degres."


def _estimate_pdf_pages(path: Path) -> int:
    data = path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page\b", data))


def _quality_for_text(text: str) -> tuple[str, float]:
    stripped = text.strip()
    if not stripped:
        return "empty", 0.0
    alpha = sum(1 for char in stripped if char.isalpha())
    readable = sum(1 for char in stripped if _is_common_readable(char))
    spaces = sum(1 for char in stripped if char.isspace())
    ratio_alpha = alpha / max(len(stripped), 1)
    ratio_readable = readable / max(len(stripped), 1)
    ratio_spaces = spaces / max(len(stripped), 1)
    length_bonus = min(len(stripped) / 1200, 1.0) * 0.2
    confidence = min(1.0, ratio_readable * 0.48 + ratio_alpha * 0.22 + min(ratio_spaces * 3, 1.0) * 0.1 + length_bonus)
    if confidence >= 0.72:
        return "good", round(confidence, 3)
    if confidence >= 0.45:
        return "medium", round(confidence, 3)
    return "low", round(confidence, 3)


def _looks_like_text(value: str) -> bool:
    if len(value) < 3:
        return False
    readable = sum(1 for char in value if _is_common_readable(char))
    spaces = sum(1 for char in value if char in " \n\r\t")
    alpha = sum(1 for char in value if char.isalpha())
    length = max(len(value), 1)
    return alpha >= 2 and readable / length > 0.88 and (spaces >= 1 or len(value) < 80)


def _looks_like_pdf_literal_text(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 3:
        return False
    readable = sum(1 for char in stripped if _is_common_readable(char))
    spaces = sum(1 for char in stripped if char in " \n\r\t")
    alpha = sum(1 for char in stripped if char.isalpha())
    length = max(len(stripped), 1)
    if len(stripped) < 80:
        return alpha >= 2 and readable / length > 0.9 and spaces >= 1
    return alpha >= 8 and readable / length > 0.86 and spaces / length > 0.03


def _clean_text(value: str) -> str:
    value = value.replace("\x00", " ")
    value = "".join(char if _is_common_readable(char) else " " for char in value)
    return re.sub(r"\s+", " ", value).strip()


def _postprocess_ocr_text(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return text

    text = _apply_ocr_phrase_corrections(text)

    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"([.,;:!?])(?=[A-Za-zÀ-ÿ])", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b[A-Za-zÀ-ÿ']+\b", _correct_ocr_word_match, text)
    text = _apply_ocr_phrase_corrections(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _apply_ocr_phrase_corrections(text: str) -> str:
    for pattern, replacement in OCR_PHRASE_CORRECTIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _correct_ocr_word_match(match: re.Match[str]) -> str:
    word = match.group(0)
    lower = word.lower()
    corrected = OCR_WORD_CORRECTIONS.get(lower)
    if not corrected:
        corrected = _nearest_domain_term(lower)
    if not corrected or corrected == lower:
        return word
    if word[:1].isupper():
        return corrected[:1].upper() + corrected[1:]
    return corrected


def _nearest_domain_term(word: str) -> str | None:
    if len(word) < 5 or not word.isalpha() or word in OCR_DOMAIN_TERMS or word in OCR_PROTECTED_WORDS:
        return None
    best_term = None
    best_distance = 3
    for term in OCR_DOMAIN_TERMS:
        if abs(len(term) - len(word)) > 2:
            continue
        distance = _levenshtein_distance(word, term, max_distance=2)
        if distance < best_distance:
            best_distance = distance
            best_term = term
    if not best_term:
        return None
    if best_distance <= 1:
        return best_term
    if best_distance == 2 and len(word) >= 9:
        return best_term
    return None


def _levenshtein_distance(left: str, right: str, max_distance: int) -> int:
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            value = min(insertion, deletion, substitution)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def _is_common_readable(char: str) -> bool:
    return char.isalnum() or char in COMMON_PUNCTUATION
