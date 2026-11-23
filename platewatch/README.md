# PlateWatch — Vision & PydanticAI Licence-Plate Indexing

[![CI](https://github.com/AshraHossain/agent-system/actions/workflows/platewatch.yml/badge.svg)](../.github/workflows/platewatch.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![PydanticAI](https://img.shields.io/badge/PydanticAI-1.0%2B-E92063.svg)](pyproject.toml)
[![Contracts](https://img.shields.io/badge/contracts-frozen%20Pydantic-success.svg)](src/platewatch/contracts.py)

Detect vehicle licence plates in images, read and normalize OCR text, tag confidence/format anomalies, persist embeddings in a searchable SQLite vector store, and query them through a grounded **PydanticAI** agent. Self-contained project (own `pyproject.toml`, `uv.lock`, test suite, and CI workflow); run commands from inside `platewatch/`.

---

## 1. System Architecture

### Layered System Architecture (`flowchart LR`)

```mermaid
flowchart LR
    subgraph Client["Client & CI Surface"]
        CLI["platewatch CLI (demo, ingest, search, ask, serve)"]
        API["Read-Only HTTP API (GET /plates/search, /health, /ready)"]
        PYTEST["Offline Test Suite (pytest)"]
    end

    subgraph Edge["Ingestion & Boundary Guards"]
        PIPE["PlatePipeline (Per-Image 30s Timeout, 25MP Bomb Guard, SHA-256 Dedup)"]
        AG["PydanticAI Agent + Grounded Output Validator"]
    end

    subgraph Core["Deterministic Pure Core (platewatch/core — Zero AI/Torch Imports)"]
        TXT["text.py (normalize_plate, plausible_format, tag_plate, sanitize_ocr_text)"]
        FMT["formats.py (9 source-cited regional patterns, match_regions, PLATEWATCH_REGIONS)"]
        EMB["embedding.py (HashingEmbedder with OCR-Confusable Folding O/0, I/1, B/8)"]
        STORE["store.py (SQLite PlateStore + Cosine Vector Search + Sightings Counter)"]
    end

    subgraph Adapters["Vision Protocol Adapters (platewatch/vision)"]
        PROT["Detector & Reader Protocols"]
    end

    subgraph Backends["Live Hugging Face vs. Deterministic Offline Mock"]
        LIVE[("Live Mode: YOLOS Detector + TrOCR Reader + Live LLM")]
        MOCK[("Mock Mode: ScriptedVision + FunctionModel Mock Agent")]
    end

    CLI & PYTEST --> PIPE & AG
    CLI --> API
    API -- "read-only: search, count" --> STORE
    PIPE --> PROT & TXT & EMB & STORE
    TXT --> FMT
    AG --> PIPE & STORE
    PROT -. "Live (--with transformers/torch)" .-> LIVE
    PROT -. "Offline Default (ScriptedVision)" .-> MOCK
    AG -. "Mock Default (FunctionModel)" .-> MOCK
```

### End-to-End Ingestion & Search Sequence (`sequenceDiagram`)

```mermaid
sequenceDiagram
    autonumber
    participant U as Operator / CLI
    participant A as PydanticAI Agent (agent.py)
    participant P as PlatePipeline (pipeline.py)
    participant V as Vision Adapter (Detector + Reader)
    participant C as Pure Core (text.py + embedding.py)
    participant S as PlateStore (SQLite + Vectors)

    rect rgb(235, 245, 255)
        Note over U,S: Phase 1 — Idempotent Image Ingestion & OCR Normalization
        U->>A: Ask "Scan inbox and find plate ABC 123"
        A->>P: Call tool scan_new_images() (inbox path fixed in PlateDeps)
        P->>S: Check has_image(image_sha256)
        P->>V: detect(image) -> BoundingBox list (isolated per 30s timeout)
        P->>V: read(crop, box) -> raw_text, ocr_confidence
        P->>C: normalize_plate(raw_text) + tag_plate(plate, confidence)
        C-->>P: Normalized plate + tags (plausible-format / odd-format / needs-review)
        P->>S: Persist frozen PlateRecord + L2-normalized n-gram vector
        P-->>A: Return frozen ScanSummary
    end

    rect rgb(240, 255, 240)
        Note over A,U: Phase 2 — Fuzzy Vector Search & Output Grounding Check
        A->>S: Call tool search_plates(plate_text="ABC 123")
        S->>C: Embed query (folding OCR-confusable characters)
        S-->>A: Ranked SearchHit list (exact matches first, cosine score, sightings count)
        A->>S: Call tool list_needs_review()
        S-->>A: Plate IDs flagged needs-review
        A->>A: Run @agent.output_validator (reject any plate_id not returned by a tool)
        A-->>U: Return validated frozen PlateReport
    end
```

---

## 2. Quick Start & Commands

```bash
cd platewatch
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                                  # 100% offline suite (ScriptedVision + FunctionModel)
uv run platewatch demo                         # end-to-end offline ingestion + search + agent demo
```

### Read-Only Search API

```bash
uv run platewatch --db var/platewatch/plates.db serve        # 127.0.0.1:8001
curl 'http://127.0.0.1:8001/plates/search?q=AB12+CDE'
curl http://127.0.0.1:8001/ready                             # {"status":"ready","plates":2}
```

| Route | Purpose |
|---|---|
| `GET /plates/search?q=<text>&limit=<1-50>` | Fuzzy plate search; returns `SearchHit` objects |
| `GET /health`, `GET /ready` | Container probes, always unauthenticated |

Every route is a read. The database path comes from the process that starts the server, never
from a request, so `q` is plate text only — normalized and embedded, never used as a path or as
SQL. Write methods are not routed (`405`), and out-of-range input is rejected with `422`.

To require a token, configure digests rather than the token itself, so an environment dump does
not leak a credential:

```bash
export PLATEWATCH_API_TOKEN_SHA256=$(python -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$MY_TOKEN")
uv run platewatch serve --host 0.0.0.0
curl -H "Authorization: Bearer $MY_TOKEN" 'http://127.0.0.1:8001/plates/search?q=AB12CDE'
```

Binding beyond loopback without a configured token is refused, rather than quietly serving an
open endpoint; `--allow-unauthenticated` overrides that only when an authenticating proxy sits in
front. **Rate limiting, per-user identity and TLS are not implemented**: put this behind a reverse
proxy that provides them before exposing it to a network.

### Regional Plate Formats (Optional)

Recognised plates gain a `region:<code>` tag. Nine regions are covered, each with its source cited
in `src/platewatch/core/formats.py`: `gb`, `fr`, `es`, `it`, `nl`, `us-ca`, `us-ny`, `us-tx`,
`us-fl`.

```bash
uv run platewatch demo                         # no region enforced: generic shape check unchanged
PLATEWATCH_REGIONS=gb,fr uv run platewatch demo   # only these regions count as plausible
```

With no region named, a plate passes on either a regional pattern or the generic shape check, so
enabling the table never rejects a plate the old check accepted. Naming regions makes the check
strict: anything outside them is tagged `odd-format` and `needs-review`.

### Live Hugging Face Vision & Live Agent (Optional)

```bash
# Ingest a directory of images with real Hugging Face YOLOS + TrOCR models
uv run --with transformers --with torch platewatch ingest <image_dir>

# Run live model accuracy evaluation against your own labelled photos (named <PLATE>.jpg)
PLATEWATCH_LIVE_DIR=<photos_dir> uv run --with transformers --with torch \
  pytest -m live tests/test_platewatch_live.py -s
```

---

## 3. Architectural Invariants & Honest Limits

- **Framework-Free Pure Core**: `src/platewatch/core/` never imports `pydantic_ai`, `torch`, `transformers`, or `google` (enforced by `tests/test_platewatch.py`).
- **Frozen Contracts**: Every model in `src/platewatch/contracts.py` (`BoundingBox`, `RawDetection`, `PlateRecord`, `SearchHit`, `ScanSummary`, `PlateReport`) enforces `ConfigDict(frozen=True)`.
- **No Model-Supplied Paths/SQL**: Agent tools take no file paths, URLs, or SQL from the model; the inbox directory is fixed in `PlateDeps`, and `@agent.output_validator` rejects any `plate_id` not returned by a tool in the current run.
- **A Format Pattern Is a Signal, Never a Gate**: a regex cannot be correct for any of these
  regions. Every one keeps older sequential formats legally on the road, issues personalised marks
  of arbitrary shape, and runs separate schemes for motorcycles, trailers, dealers, government and
  diplomatic vehicles, so any pattern narrow enough to carry information will reject legitimate
  plates. `tag_plate` therefore flags and never discards. Germany is deliberately absent: once
  separators are normalised away, `B-AB 1234` and `BA-B 1234` are the same string, and what remains
  collides with the New York, Texas and Florida patterns.
- **Honest Limits**: Default Hugging Face model weights (`nickmuchi/yolos-small-finetuned-license-plate-detection`, `microsoft/trocr-base-printed`) require live photo evaluation before quoting accuracy; the offline embedder is lexical (character n-grams with OCR-confusable folding), and search is an exact cosine scan suited for up to ~10^5 plates.
