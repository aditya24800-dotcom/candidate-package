# Running the RFQ inbox parser

## Setup: one step

You need Python 3.11 or newer.

Every command in this file is run from the repository root: the folder that holds
`rfq_parser/`, `data/`, `requirements.txt` and this file. After a clone or unzip, `cd` into
that folder first.

```
pip install -r requirements.txt
```

That installs three packages: `openai`, `openpyxl` and `pypdf`. Nothing else is needed, and
there is no Docker image because nothing here assumes Linux.

To keep the packages out of the system Python, make a virtual environment first:

| | Windows | Linux, WSL |
|---|---|---|
| Create | `python -m venv .venv` | `python3 -m venv .venv` |
| Activate | `.venv\Scripts\activate` | `source .venv/bin/activate` |

Developed and run on Windows 11 with Python 3.14.8. The code uses no syntax newer than 3.11,
but it has not been run on 3.11.

## Run

From the repository root:

```
python -m rfq_parser parse --emails <dir> --out parsed.json
```

`<dir>` and the `--out` file can be anywhere, given as relative or absolute paths. The
command itself must be started from the repository root, because it imports the provided
helpers in `data/` and reads `data/catalog.csv`. Started from another folder it stops with
`No module named rfq_parser`.

For the dev inbox:

```
python -m rfq_parser parse --emails data/dev/emails --out parsed.json
python data/check_format.py parsed.json --emails data/dev/emails
```

To set the key for a live run:

| Shell | Command |
|---|---|
| Windows cmd | `set OPENAI_API_KEY=...` |
| PowerShell | `$env:OPENAI_API_KEY="..."` |
| bash, WSL | `export OPENAI_API_KEY=...` |

What it writes:

| File | Content |
|---|---|
| `parsed.json` (the `--out` path) | One record per email, in the shape `data/README.md` defines, plus the optional `sku`, `match` and `match_reason` on each line |
| `parsed.cost.json` (next to `--out`) | Model calls, live calls, tokens and dollars, in total and per email |
| `parsed.seen.json` (next to `--out`) | Which emails this and earlier runs read: id, `Message-ID`, class. Not in git |
| stderr | One line per email as it is read: class, line count, and the review reason if flagged |

Exit code is 0 when the output was written, and 2 when `--emails` is not a folder.

## Model and key

| Setting | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | none | The key for the default provider, OpenAI |
| `RFQ_LLM_MODEL` | `gpt-6.1-terra` | The model. One model does both jobs: transcribing pictures and scans, and reading the email |
| `RFQ_LLM_PROVIDER` | `openai` | Set to `anthropic` to use the optional second provider (see below) |
| `RFQ_LLM_CACHE_DIR` | `rfq_parser/llm/llm_cache/` | Where model answers are saved |

The model is called at reasoning effort `low`, with `store` off so emails are not kept on the
provider's side.

## Re-runs and running without a key

Every model answer is saved in `rfq_parser/llm/llm_cache/`, one JSON file per request, named by a hash of the
whole request: provider, model, prompt, answer shape and input. A second run over the same
inbox finds every answer there and makes no model calls. Change the prompt, the model or an
email, and only the affected requests are asked again.

With no key, a request that is in the cache is answered from it. A request that is not is
treated like any other model failure, below.

That folder holds the answers for the dev inbox, so the dev command above
runs with no key and makes no model calls. Checked: a run with the key and a run without it
wrote identical `parsed.json` files.

## The same email delivered twice

Duplicates are found before the model is called, so a duplicate costs nothing.

- **In one run:** the same `Message-ID`, or the same request sent again by the same sender.
- **In a later run:** an email whose `Message-ID` an earlier run already read is a duplicate of
  that first delivery. `duplicate_of` is then an id from the earlier run. The memory is
  `parsed.seen.json`, so both runs must use the same `--out`. Delete that file to forget.
- **Reading the same file again** is not a second delivery: a re-run over the same folder gives
  the same output.
- **Not covered:** a resend with a new `Message-ID` that arrives in a later run. Across runs only
  the exact `Message-ID` counts, so that a repeat order next month is never emptied.
- An email the model could not read is not remembered, so its next delivery is read.

## When something fails

No email can stop the run, and every email in the folder gets a record.

| Situation | What the record says |
|---|---|
| No key and no saved answer, or the provider call fails | `rfq`, `needs_review: true`, reason "The model was not available to read this email" |
| The `.eml` file cannot be parsed | `rfq`, `needs_review: true`, with the parse error |
| An attachment is damaged, password-protected or of a type nothing here reads | The email is still read; the model is told the attachment could not be opened and flags the email |
| The email is plainly a bounce or an out-of-office, and the model is unavailable | `auto_reply`, no flag |

The fallback class is `rfq` on purpose. A false "needs review" costs a person a minute; a
false "nothing to do" loses a customer's request.

## What a run costs

Measured on the dev inbox (24 emails) with the default provider and model, OpenAI
`gpt-6.1-terra`:

| | |
|---|---|
| Model calls | 30: 23 to read emails, 7 to transcribe pictures and scans (a scanned PDF is one call per page). One email is a duplicate and is never sent to the model |
| Tokens | 56,822 in, 14,892 out |
| Cost | $0.10 in total, $0.004 per email on average |
| Most expensive emails | E009 $0.022, E006 $0.016, E021 $0.015 (a 32-row scan, and lists of 44 and 50 lines) |
| Cheapest emails | under $0.001 each (no line items) |
| Time | about 3.5 minutes live; about 1 second on a re-run from cache |

A long attachment costs more, because it is read in parts of about a page with one model call
per part, and there is no page limit. Measured: one email with a 300-page text PDF (6,300 item
rows) took 150 calls, $2.21 and 77 minutes, and returned all 6,300 lines.

The same inbox on the optional Anthropic provider (`claude-opus-5-5`) cost $0.82.

Prices are in `rfq_parser/llm/openai_provider.py` and `rfq_parser/llm/anthropic_provider.py`.
`parsed.cost.json` is regenerated on every run, and reports what each answer cost when it was
first computed, so it reads the same on a replay from cache.

## Check that it works

```
python diagnostics/run.py
```

This runs about 70 checks with no key and no model calls, and changes nothing in the repository:
the environment, the `parse` command on the dev inbox, accuracy against the answer key, broken
emails and attachments, a failing provider, each component, the web app's endpoints, and a
scan for stored API keys. It prints PASS or FAIL per check. Unlike `parse`, it can be started
from any folder.

## Optional: the web app

A small local app that walks one email through the system:

```
python -m rfq_parser web --emails data/dev/emails
```

Then open `http://127.0.0.1:8000`. It runs the same pipeline as `parse` (from the saved
answers, so the dev inbox needs no key), listens on this machine only, and uses nothing
outside the Python standard library.

- Left: the inbox, with each email's class, line count, and a mark for review or duplicate.
- Middle: the email as received with its body split into current, quoted and forwarded parts;
  each attachment with how it was read (by code, or transcribed by the model) and the text
  that came out; the text the model was sent; and what the system concluded.
- Right: the line items with their catalog decision and its reason. Click a line to highlight
  where it came from. Accept the decision, pick another part (from the listed candidates or a
  catalog search), or mark the line as not stocked.

The rep's choices are saved to `decisions.json` (change the path with `--decisions`, the port
with `--port`). `parsed.json` is never changed by the app.

## Optional: the Anthropic provider

Switched off by default, and never used unless you ask for it:

```
pip install anthropic
set RFQ_LLM_PROVIDER=anthropic
set ANTHROPIC_API_KEY=...
python -m rfq_parser parse --emails data/dev/emails --out parsed.json
```

(Those `set` lines are for Windows cmd. In PowerShell write `$env:NAME="value"`, in bash
`export NAME=value`.)

The default model on that path is `claude-opus-5-5`. Saved answers are kept apart per
provider, so answers from one are never served on a run with the other.

## Not done yet

- The code has been run on Python 3.14.8 only, not on 3.11.
- The screen recording is not linked here yet.
