# Token Optimizer: Controlled A/B Benchmark

This is a **controlled experiment**, not a real-world usage report. For field numbers from a
real user's 30 days, see [`BENCHMARK.md`](./BENCHMARK.md) — the two are different lenses, not
competing figures. This report answers a narrower, causal question: run the **same task** with
Token Optimizer on and off, and measure whether it **costs or saves real billed tokens**, one
mechanism at a time.

## What it is

A controlled A/B test. The exact same task is run many times with Token Optimizer **on** and
**off** (20 runs each side), and we count the **real tokens Claude billed** from its own session
logs, not the plugin's dashboard. Because only one thing changes between the two piles, any
difference is Token Optimizer and nothing else. Every result also passes a **comprehension
check**: the model is asked a question whose answer is buried in the compressed output, so a
"saving" bought by blinding the model is caught and thrown out.

## What it is not

**This is not a "Token Optimizer saves X% in every session" number.** It does not produce a
single blended per-session figure, and it should never be read as one.

It measures each compression mechanism **in isolation**, on a workload built to exercise that one
mechanism. Your real saving in any given session depends entirely on **how much compressible
output that session happens to contain**, which varies enormously. A session full of large
command output and searches saves a lot; a short chat-only session has almost nothing to
compress. Both are honest. Neither is "the number."

## What it can't measure — and it's a lot

This benchmark only measures the **always-on automatic compression** — the tool output Token
Optimizer squeezes in the background, every turn, with no action from you. That is deliberately
the narrowest, most provable slice. A whole category of real savings sits **outside** it, because
a fixed-model, short-session A/B structurally cannot see them:

- **Model routing** — running routine turns on a lighter, cheaper model. The benchmark holds the
  model fixed on each side, so it is blind to this by design.
- **Cache-drop avoidance** — not invalidating Claude's prompt cache, which would otherwise re-bill
  your whole context at full price. This pays off across long, multi-turn sessions; the benchmark
  runs short ones.
- **The savings audit you run** — the dashboard and coach recommendations, which depend on you
  acting on them, so an automated A/B cannot score them.
- **The structural audit** — recommendations to slim down the skills, commands, and hooks that
  bloat every session. Advisory and user-driven.

So read the per-mechanism numbers as a **floor** — the automatic part we can prove. The routing
and behavioral savings stack on top.

## How we measured

Described in enough detail to be independently reproduced.

- **The test.** The same fixed task is run **20 times with the plugin on and 20 times off**, in
  fresh isolated working directories, with the two sides interleaved and one warm-up run per side
  discarded.
- **Ground truth.** Each run's **real billed token counts** are read from Claude's own session
  transcript, summed by token class (input, cache-read, cache-creation) and **de-duplicated by
  message** so one assistant message is never counted twice. We never read the plugin's own
  dashboard.
- **Two layers.** (A) the size of the exact tool output the plugin compressed, which proves the
  mechanism fired; (B) the whole session's input footprint — the **actually-billed saving**, which
  lags because a smaller tool result also shrinks every later turn's re-billed context.
- **The no-harm check.** Every run is asked a question whose answer lives *inside* the compressed
  output, so any "saving" bought by degrading the model's answer is caught and discarded.
- **Setup.** Claude Haiku 4.5, cross-checked on Sonnet 5. 20 runs per side. Runs that did not do
  equal work were quarantined before comparison.

## What it tests, and what each test found

Per-mechanism, measured on the shipped plugin. Token figures are hard counts; the dollar figure is
an estimate weighted from public token rates, not a billed invoice.

| The question | The A/B setup | Result | Meaning |
|---|---|---|---|
| **Does it tax a short session?** | A fresh session with nothing to compress | +~160 tokens (<0.5%) | No harm. It does not tax light sessions. |
| **Does it save on command-heavy work?** | `git status/diff/log` + `ls -la`, on vs off | **−11% tokens** / −19% dollars (est.) | Clean saver, 100% comprehension. Held on Sonnet 5 (−10.7%). |
| **Does it save on search-heavy work?** | A `grep` returning 480 matches, on vs off | **−15.7% tokens** | Clean saver, 100% correct on both the count and specific-file questions. Direction and correctness held on Sonnet 5. |
| **Does it save on re-reads?** | Read a file, edit it, read it again | Inconclusive | Reported as an honest measurement gap, not a claim. |

## How to read these numbers

- **They are per-mechanism, not per-session.** "−11% on command-heavy work" means exactly that:
  on a session dominated by verbose command output. It is not a promise about your Tuesday.
- **The floor is the point.** The strongest honest claim is the combination: it **never
  meaningfully taxes** a light session, and it **genuinely saves** once there is real output to
  compress.
- **Dollars and tokens differ.** The dollar figure runs higher than the token figure because the
  compression removes the most expensive class of tokens first. It is an **estimate**; the token
  number is the hard count.
- **The method is fully described** above, so the test can be independently reproduced.

---

Looking for real-world numbers from actual usage instead of a controlled experiment? See
[`BENCHMARK.md`](./BENCHMARK.md).
