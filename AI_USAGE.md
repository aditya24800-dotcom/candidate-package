# AI usage

## Setup

- **Tool:** Claude Code, in the terminal and in PyCharm. PyCharm in one early session.
- **Models:** Claude Opus 5.5 for a first attempt (13 Nov), Claude Fable 5.2 for the build handed in (14–15 Nov).
- **Instruction files:** no project `CLAUDE.md`; one global file, `RTK.md`, which only shortens command output. No subagents.
- **Skills:** `ponytail`, `ponytail-audit`, `ponytail-review` (smallest change, audit, review), `claude-api`, `caveman`, `claude-in-chrome` (the web app in a browser).
- **Model inside the parser:** OpenAI `gpt-6.1-terra`, Responses API, strict JSON schema, effort `low`, `store` off, for transcription and for reading. Dev: 30 calls, 56,822 tokens in, 14,892 out, $0.10.
- **Keys:** the agent ran on my own account. The parser first ran on my own Anthropic key, to spare the $20 cap, then on the provided OpenAI key, which produced `parsed.json`.

## How I worked

I spent about half of my time planning. I broke the problem into small tasks and worked out which steps plain code could handle, so the model was only used where it was needed and token cost stayed low. I explained the overall goal to the coding agent first, then built the modules one at a time. After each module I checked its output where I could, and I tracked every change and tested it, so I could see that progress was real and matched what the task needed. About 20% of the time went to writing code. The remaining 30% went to testing and to making changes based on the test results and on my reading of the emails.

## Three moments

**The agent was wrong.** After the first test of all 24 emails it reported 24/24 and precision 0.995. Its scorer had left the duplicate, E008, out of the line totals. I asked how every email could pass when duplicates were not detected. Counted in full, precision was 0.823, with 45 invented lines; that led to the duplicate step. Session `84edf70f`, at "if duplicate emals and all are not properly detected how all 24 email tested fine?". Commit `f143633`.

**I overruled it.** The agent recommended deciding duplicates after the model, by comparing extracted lines. I had the whole check put in the pre-parser, so a repeated email is never sent to the model. Session `84edf70f`, at "add a step in preparser to detect any dumplicate". Commit `f143633`.

**It saved real time.** With the model code the agent built a cache: each answer is saved under a hash of the whole request. A live dev run takes 3.5 minutes and $0.10; from the cache, 1 second and nothing. It is also what lets the inbox run with no API key. Session `84edf70f`, at "ok create a llm folder and add all LLM related codes in that". Commit `f143633`.

**Sessions in `ai-sessions/`:** `84edf70f` is the build handed in, continuing from `f1fec2fd`. `72bc5328` is a first attempt on Opus 5.5, not kept. `84d102e5` connects GitHub. `d12f43af` and `605abde8` are short starts.
