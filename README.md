# Plateforme documentaire - V1 locale

Prototype local sans dependances externes obligatoires pour tester :

- gestion clients, affaires, documents, taches et alertes ;
- depot PDF / image / texte ;
- detection de couche texte exploitable ;
- OCR optionnel si `rapidocr` ou `tesseract` est installe ;
- extraction PDF optionnelle si `pdftotext` est installe ;
- OCR des PDF scannes via rendu image `pdftoppm` + RapidOCR ou Tesseract ;
- essais de modes OCR et corrections legeres d'inclinaison pour choisir le meilleur resultat ;
- controle qualite base sur les confiances OCR et la lisibilite du texte, pas seulement sur la presence de caracteres ;
- indexation par page ;
- recherche plein texte + rapprochement semantique simple ;
- filtrage des resultats selon les droits de l'utilisateur de demonstration.

## Lancer en local

```bash
source .venv/bin/activate
DOCUMENT_OCR_ENGINE=auto DOCUMENT_EMBEDDING_PROVIDER=off python -m app.server
```

Ouvrir ensuite :

```text
http://127.0.0.1:8000
```

Si le port `8000` est deja utilise :

```bash
DOCUMENT_OCR_ENGINE=auto DOCUMENT_EMBEDDING_PROVIDER=off python -m app.server 8001
```

Ouvrir alors `http://127.0.0.1:8001`.

Les donnees sont creees dans `data/store.json` et les fichiers originaux dans `uploads/`.

## Ameliorer l'OCR local

Le prototype fonctionne sans installation supplementaire pour les documents texte. Pour les scans, utiliser RapidOCR local en priorite :

```bash
source .venv/bin/activate
python -m pip install -r requirements-local-ai.txt
```

Tesseract reste utile en moteur de secours :

- installer `tesseract` pour les images ;
- installer `poppler` afin d'obtenir `pdftotext` pour les PDF.

Sur macOS avec Homebrew :

```bash
brew install tesseract poppler tesseract-lang
```

`tesseract-lang` ajoute notamment le modele francais. Sans lui, les documents francais peuvent etre lus avec une qualite degradee.

## Activer RapidOCR et BGE-M3

Pour une version locale plus proche de la cible :

> Recommande : utiliser Python 3.12 pour cette pile locale IA. Python 3.14 est trop recent pour plusieurs packages ML/OCR locaux.

Si Python 3.12 n'est pas disponible :

```bash
brew install python@3.12
```

Creer l'environnement virtuel :

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Installer l'OCR local et la recherche semantique :

```bash
python -m pip install -r requirements-local-ai.txt
```

Puis lancer l'application :

```bash
DOCUMENT_OCR_ENGINE=auto DOCUMENT_EMBEDDING_PROVIDER=auto python -m app.server
```

Comportement :

- si `rapidocr` est installe, il est utilise en priorite pour l'OCR local ;
- si `paddleocr` est installe dans un environnement compatible, il peut aussi etre utilise ;
- sinon, l'application garde Tesseract en secours ;
- si `sentence-transformers` est installe, le modele `BAAI/bge-m3` est utilise pour vectoriser les pages et les requetes ;
- sinon, la recherche plein texte actuelle continue de fonctionner.

La premiere utilisation de BGE-M3 telecharge le modele dans le cache local. Ensuite, pour une execution hors ligne, garder ce cache sur la machine de production et lancer avec :

```bash
TRANSFORMERS_OFFLINE=1 DOCUMENT_EMBEDDING_PROVIDER=auto python3 -m app.server
```

Pour recalculer OCR et embeddings sur les documents deja importes :

```bash
python3 scripts/reindex_all.py
```

## Chemin de deploiement

Cette V1 est volontairement simple. Pour une application de production, remplacer progressivement :

- `data/store.json` par PostgreSQL ;
- `uploads/` par S3, MinIO ou stockage objet equivalent ;
- le traitement synchrone par une file de jobs ;
- la recherche locale par OpenSearch et/ou `pgvector` ;
- l'utilisateur de demonstration par une authentification reelle.
