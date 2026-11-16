# Architecture: RFQ inbox parser

## Overview

```mermaid
flowchart LR
    EML[".eml folder"] --> CLI["cli.py<br/>parse / web"]
    CLI --> PIPE["pipeline.parse_inbox"]
    PIPE --> OUT["parsed.json<br/>parsed.cost.json<br/>parsed.seen.json"]
    CLI -. "web" .-> WEB["web/server.py<br/>App + HTTP handler"]
    WEB --> PIPE
    WEB --> UI["browser page<br/>127.0.0.1:8000"]
    UI -- "rep decisions" --> DEC["decisions.json"]
    CAT["data/catalog.csv"] --> INT
```

## Pipeline (`rfq_parser/pipeline.py`)

```mermaid
flowchart TD
    A["1. MimeParser<br/>unpack each .eml"] --> AT["AttachmentExtractor<br/>read attachments"]
    AT --> RD["readers: xlsx, delimited,<br/>sniff, decode_text"]
    AT -- "image / scan / PDF" --> TR["llm.transcribe"]
    A --> B["2. preparser.find_duplicates<br/>this inbox + parsed.seen.json"]
    B -- "duplicate" --> DUP["duplicate record<br/>no model call"]
    B -- "new" --> C["3. render_calls<br/>model input text, split if long"]
    C --> RE["llm.read_email<br/>one call per part"]
    RE --> IN["4. Interpreter.interpret<br/>shape + catalog match"]
    IN --> D["5. _link_revisions<br/>thread headers"]
    DUP --> D
    D --> OUT["records by email id"]
```

Failure rules:

- `_read_one` catches every exception. An email the model cannot read gets `fallback_record`: class `rfq`, `needs_review: true`. Exception: bounces and out-of-office mail become `auto_reply`.
- An email with no model answer is not written to `parsed.seen.json`, so its next delivery is read.

## LLM layer (`rfq_parser/llm/`)

```mermaid
flowchart TD
    T1["tasks.read_email"] --> CL
    T2["tasks.transcribe"] --> CL
    CL["client.LLMClient.complete"] --> KEY["request_key<br/>hash of provider, model, prompt, schema, input"]
    KEY --> CACHE{"ResponseCache<br/>llm_cache/*.json"}
    CACHE -- "hit" --> LED["calls ledger<br/>from_cache = true"]
    CACHE -- "miss" --> PROV{"RFQ_LLM_PROVIDER"}
    PROV -- "openai (default)" --> OAI["OpenAIProvider<br/>OPENAI_API_KEY"]
    PROV -- "anthropic" --> ANT["AnthropicProvider<br/>ANTHROPIC_API_KEY"]
    OAI --> SAVE["cache.put"]
    ANT --> SAVE
    SAVE --> LED
    CACHE -- "miss and no key / call fails" --> UNA["LLMUnavailable"]
    UNA --> FB["pipeline fallback_record"]
```

- Model: `RFQ_LLM_MODEL`, default `gpt-6.1-terra` (OpenAI). Effort `low`, `store` off.
- Cost: `cost_usd` uses `PRICES` from both provider modules. `cost_report` builds `parsed.cost.json` from the ledger, including cached calls.

## Interpreter (`rfq_parser/interpreter/`)

- `Interpreter.interpret(answer)` shapes the record. Request classes are `rfq`, `rfq_revision`, `purchase_order`. A request with no lines is forced to `needs_review`.
- `match(description)` per line, code only:
  - `attributes.parse` reads product, size, spec, material, ends, extras from text.
  - Filters in order: size, spec, material, ends, feature. Each filter that removes every part returns `not_in_catalog` with the reason.
  - One part left: `matched`. Several: `ambiguous` with up to 12 candidates.
- Catalog: `catalog.load_catalog` reads `data/catalog.csv`.
- No line is ever dropped.

## Web app (`rfq_parser/web/`)

- `App` runs `parse_inbox` once at start and keeps `trace` (unpacked email, model input, model answer) plus attachment files in a temp folder.
- `ThreadingHTTPServer` on `127.0.0.1` only. Routes: `/api/inbox`, `/api/email/<id>`, `/api/catalog?q=`, `/file/<id>/<n>`, `POST /api/decision`.
- `decide` writes the rep's choice to `decisions.json`. System records are never changed.
- Web runs without `seen_file`, so it does not use cross-run duplicate memory.

## Files

| Path | Role |
|---|---|
| `rfq_parser/cli.py` | `parse` and `web` commands, writes outputs |
| `rfq_parser/pipeline.py` | the run: order of work, fallbacks, revision links, cost report |
| `rfq_parser/mime_parser.py` | `.eml` to structured email |
| `rfq_parser/attachments/` | attachment extraction and readers |
| `rfq_parser/preparser.py` | duplicate detection, body text, part preparse |
| `rfq_parser/render.py` | model input text, split into calls |
| `rfq_parser/llm/` | model client, providers, cache, tasks, prompts, schemas |
| `rfq_parser/interpreter/` | record shaping and catalog matching |
| `rfq_parser/web/` | local review app |
| `data/` | catalog, format checker, record contract (`data/README.md`) |
| `diagnostics/` | `run.py` checks, `long_pdf.py` live test |
| `rfq_parser/llm/llm_cache/` | saved model answers, enables no-key replay |

## Outputs

| File | Written by | Content |
|---|---|---|
| `parsed.json` | `cli.parse` | one record per email |
| `parsed.cost.json` | `cli.parse` | calls, tokens, dollars per email |
| `parsed.seen.json` | `pipeline._save_seen` | Message-ID memory across runs |
| `decisions.json` | `web.App.decide` | rep decisions per line |
