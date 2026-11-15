# AI usage

## Setup

- **Tool:** Claude Code, in the terminal and in PyCharm.
- **Models:** Claude Opus 5.5 for a first attempt on 13 November. Claude Fable 5.2 for the
  build that is handed in, on 14 and 15 November.
- **Instruction files:** no project `CLAUDE.md`. One global file, `RTK.md`, which only
  shortens command output. No subagents.
- **Skills:** `ponytail-audit` and `ponytail-review` (audit of the whole repository, review of
  uncommitted changes), `claude-api` (for the optional Anthropic provider), `caveman` (short
  replies), `claude-in-chrome` (to check the web app in a real browser).
- **MCP servers:** Claude in Chrome. The PyCharm server in one early session.
- **Model inside the parser:** OpenAI `gpt-6.1-terra`, the default, through the Responses API
  with a strict JSON schema, reasoning effort `low`, and `store` off. One model does both jobs:
  transcribing pictures and scans, and reading the email. The dev inbox takes 29 calls
  (23 emails read, 6 transcriptions), 56,266 tokens in and 15,083 out, for $0.10.
- **Keys:** the agent ran on my own account. The parser was first exercised with my own
  Anthropic key (`claude-opus-5-5`), to spare the $20 cap, and then with the provided OpenAI
  key. `parsed.json` and the saved answers in `rfq_parser/llm/llm_cache/` are from the OpenAI run.

## How I worked

I spent about half of my time planning. I broke the problem into small tasks and worked out which steps plain code could handle, so the model was only used where it was needed and token cost stayed low. I explained the overall goal to the coding agent first, then built the modules one at a time. After each module I checked its output where I could, and I tracked every change and tested it, so I could see that progress was real and matched what the task needed. About 20% of the time went to writing code. The remaining 30% went to testing and to making changes based on the test results and on my reading of the emails.

## Three moments

**The agent was wrong.** After the first test of all 24 emails it reported 24/24 and a
precision of 0.995. Its scorer had left the duplicate email, E008, out of the line totals. I
asked how every email could pass when duplicates were not detected. Counted with nothing left
out, precision was 0.823, with 45 invented lines. That is what led to the duplicate step in
`rfq_parser/preparser.py`.
Session `84edf70f`, at "if duplicate emals and all are not properly detected how all 24 email
tested fine?". Commit `f143633`.

**I overruled it.** The agent recommended deciding duplicates after the model, by comparing
the extracted lines, and catching only an identical `Message-ID` before it. I had the whole
check put in the pre-parser instead. A repeated email is now never sent to the model, and
E008 costs no model call.
Session `84edf70f`, at "add a step in preparser to detect any dumplicate". Commit `f143633`.

**It saved real time.** The cache. When I asked for the model code, the agent built the cache
with it: every model answer is saved to a file named by a hash of the whole request, so a
changed prompt, model or email asks again and nothing else does. A live run of the dev inbox
takes about 3 minutes and costs $0.10. From the cache it takes about 1 second and costs
nothing. Every later test, the full diagnostic and the web app ran from it, and it is also
what makes the inbox run with no API key.
Session `84edf70f`, at "ok create a llm folder and add all LLM related codes in that". Commit
`f143633`.

## Sessions in `ai-sessions/`

| File starts with | What it holds |
|---|---|
| `d12f43af`, `605abde8` | Two short starts: a prompt and a file reference each |
| `72bc5328` | The first attempt, on Opus 5.5, not kept |
| `84d102e5` | Connecting the GitHub repository and branch |
| `f1fec2fd` | The start of the build: the agent reading the package before any code |
| `84edf70f` | The build that is handed in, continuing from `f1fec2fd` |
