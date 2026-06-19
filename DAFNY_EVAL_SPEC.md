# DAFNY_EVAL_SPEC.md
### Technical Specification & Infrastructure Strategy — *Can frontier models write **verifiable** code?*

> **Project codename:** `dafny-eval` &nbsp;·&nbsp; **Author role:** Principal AI Research-Infrastructure Architect
> **Date:** 2026-06-18 &nbsp;·&nbsp; **Status:** DRAFT — awaiting infra sign-off (§7)
> **One-line thesis:** Frontier LLMs emit *fluent, type-correct* Dafny but fail at the part that actually matters — **loop-invariant maintenance** — and an SMT verifier exposes a class of error that BLEU/BERTScore and unit tests structurally cannot.

---

## 0. Executive summary (TL;DR)

We are building a **small, reproducible, behavioural benchmark** that feeds LLM-generated Dafny through the real verifier (`dafny verify` → Boogie → Z3) and classifies *exactly how* each model fails. This is not a DafnyBench competitor; it is an **instrument** that measures the gap between *"resolves / looks correct"* and *"is proven correct."*

| Decision | Recommendation | Rationale |
|---|---|---|
| **Where to run v1** | **Environment A — local macOS** | The whole v1 (≤5 problems × ≤4 models × k samples ≈ 10²–10³ verifications, seconds each) fits on a laptop. HPC is over-engineering for a weekend sprint. |
| **HPC role** | **Aire = documented scale-out**, not v1 | Verification is **CPU/RAM-bound (Z3), not GPU**. Aire earns its place only when N explodes *or* we self-host open-weight generation on GPU. |
| **Classify by** | **stdout parsing**, never exit code | Dafny's exit code is *officially unspecified* (Reference Manual). |
| **Experiment design** | **Annotation-infill** (DafnyBench-style) primary | Fixing the `ensures` ourselves removes the "model weakens the spec to cheat" confound. |
| **Build co-pilot** | **Claude Opus 4.8 via Claude Code** | Strongest agentic coder; using it *is* the JD signal ("AI-natives… Claude Code"). |
| **Test subjects** | 1 proprietary frontier reasoner + 2 open-weight (reasoning vs code-instruct) | The interesting axis is *reasoning vs non-reasoning* on invariant maintenance. |

**The decision I need from you is in §7.**

---

## 1. Context & scientific question

**Why this exists.** Reasonable AI's named project is *"designing evals for state-of-the-art coding models"* and *"post-training paradigms grounded in formal methods."* This benchmark sits exactly there, and on top of your real edge (LLM-as-judge eval, `ragdrift`, the arXiv τ paper). It converts "ML engineer, zero formal-methods signal" into "the close-but-unusual profile they explicitly want."

**Hypothesis (falsifiable).**
> *H₁:* Across models, a large fraction of completions **resolve** (parse + type-check cleanly) yet **fail verification**, and within those failures, the dominant category is **loop-invariant maintenance** (`invariant ... might not be maintained by the loop`) and **termination** (`decreases`), not syntax.
>
> *H₂:* Explicit-reasoning models (extended thinking / R-series) close the *entry* gap but still miss *maintenance* on the binary-search-class problems.

**The test in one paragraph.** For each benchmark problem we provide a *fixed, strong* specification (signature + `requires` + `ensures`) and the algorithm body, with all `invariant` / `assert` / `decreases` annotations **stripped**. The model must restore the annotations so that `dafny verify` succeeds. We sample *k* completions per (problem, model), run each through a pinned Dafny toolchain in a subprocess, and classify the outcome into a failure taxonomy. Primary metrics: **pass@1 / pass@k** and the **failure-category distribution** — with the headline being *"% of type-correct completions that the SMT solver rejects, and how."*

---

## 2. The engineering playbook (what to build)

### 2.1 Dafny under the hood — what makes CLI verification non-trivial

Dafny is a *verification-aware* language: it translates your annotated program into **Boogie** (an intermediate verification language), which emits **verification conditions** discharged by the **Z3 SMT solver** (Dafny 4.x bundles Z3 4.12.x; overridable via `--solver-path`). Crucially you can **verify without compiling** — we never execute the generated code, so this is sandbox-safe by construction.

Five concrete challenges the harness must handle:

1. **Exit code is unreliable.** The Dafny Reference Manual states the process exit code is *not formally specified*. (Historically 0/1/2/3/4 mapped loosely to success / CLI / resolution / compile / verification, but it is explicitly subject to change.) → **We classify by parsing stdout**, using exit status only as a coarse "0 = clean, ≠0 = something failed" backstop.
2. **`verify` ≠ `compile` ≠ `resolve`.** Use the verification-only subcommand so we measure *proof*, not codegen:
   ```bash
   dafny verify problem.dfy --cores:1 --verification-time-limit:20
   ```
   `resolve` stops after type-checking (useful to separate "type-correct" from "proven"); `build`/`run` would invoke a backend we don't want.
3. **Non-termination & timeouts.** SMT solving can hang or blow up. Two-layer defence: Dafny's internal **`--verification-time-limit:<s>`** (per-VC) **and** an OS-level **`subprocess timeout=`** hard wall. A timeout is its own outcome category, not a crash.
4. **Verification (in)stability.** Z3 proofs are sensitive to trigger selection and can be **non-deterministic across runs/machines**. For a *benchmark* this is a confound. Mitigate with `--cores:1`, a pinned Z3, a fixed random seed, and (optionally) re-running each verification *m* times to report a stability rate. Dafny's own `measure-complexity` exists precisely because of this.
5. **Extracting code from prose.** Models return Markdown with commentary. The extractor must pull fenced ```` ```dafny ```` blocks (fallbacks: bare ```` ``` ````, then whole-text), pick/concatenate sensibly when there are several, and reject empty output — *before* anything touches the toolchain.

> **Spec-gaming confound (the subtle one).** If we let the model write its *own* `ensures`, it can "verify" by writing a trivially-true postcondition. The **annotation-infill** design (we own the `ensures`; the model only supplies invariants/asserts/decreases) closes this. This is why DafnyBench and Clover use it, and why we do too.

### 2.2 The Dafny ecosystem — what to mine vs reinvent

| Source | What we take | Use |
|---|---|---|
| **`dafny-lang/dafny` → `Test/`** | Hundreds of verified `.dfy` programs | Ground-truth reference solutions to strip-and-infill |
| **DafnyBench** (Loughridge et al.) | The `fill_annotations` task design + prompt style; ~750 programs | Design validation; optional extended corpus in v2 |
| **Clover** (Sun et al., arXiv:2310.17807) | Closed-loop "code ⇄ spec ⇄ doc" consistency idea | Prior art framing; v2 repair-loop inspiration |
| **DafnyPro** (arXiv:2601.05385) | Invariant *pruning* + hint augmentation; reports 86% on DafnyBench | Baseline to cite; not needed for v1 |
| **DafnyStandardLibraries** | Idiomatic verified utilities | Style reference for prompts |

**Positioning (honest):** we are **not** trying to beat DafnyBench's coverage. We build a *minimal, fully-reproducible behavioural slice* whose value is the **failure taxonomy + clean harness**, runnable end-to-end from one `make` target.

### 2.3 `dafny_eval.py` — harness architecture

Single CLI entry-point, internally modular. Provider-agnostic so the *same* code runs against an API (v1) or a self-hosted vLLM endpoint on Aire (v2) by swapping one adapter.

```
            ┌──────────────────────────────────────────────┐
            │  problems/*.dfy.tmpl   (fixed spec, holes)    │
            └───────────────┬──────────────────────────────┘
                            ▼
                  [1] ProblemLoader  ── builds prompt (system + spec + holes)
                            ▼
                  [2] ModelClient    ── adapter: Anthropic | OpenAI-compat | vLLM
                            ▼   (raw markdown completion)
                  [3] CodeExtractor  ── pull ```dafny block → temp .dfy
                            ▼
                  [4] DafnyRunner    ── subprocess `dafny verify` + dual timeout
                            ▼   (stdout, stderr, returncode, wall_ms)
                  [5] ResultClassifier ── regex over stdout → category enum
                            ▼
                  [6] ResultStore    ── append one JSON row per (problem,model,sample)
                            ▼
                  [7] Reporter       ── pass@k + taxonomy table + plots + README stub
```

**Result row schema (JSONL — one line per sample):**
```json
{
  "run_id": "2026-06-18T...", "problem": "binary_search", "model": "deepseek-v4",
  "sample_idx": 2, "temperature": 0.7, "dafny_version": "4.x", "z3_version": "4.12.x",
  "prompt_sha": "…", "completion_raw": "…", "extracted_code": "…",
  "returncode": 4, "wall_ms": 812, "verified": false,
  "category": "INVARIANT_NOT_MAINTAINED",
  "n_verified": 0, "n_errors": 1, "error_snippet": "this loop invariant might not be maintained…",
  "timed_out": false
}
```

**Classifier — stdout pattern → category** (exact strings pinned & validated against the installed Dafny in Stage 1):

| Category | Signal in stdout (regex, indicative) | Meaning |
|---|---|---|
| `FULL_SUCCESS` | `verifier finished with \d+ verified, 0 errors` | ✅ proven |
| `PARSE_ERROR` | `parse error|syntax error` | malformed Dafny |
| `RESOLUTION_ERROR` | `Error:.*(unresolved|duplicate|type|cannot)` *before* verifier line | type/name failure — *didn't even reach the prover* |
| `PRECONDITION_FAIL` | `precondition.*could not be proved` | bad call-site reasoning |
| `POSTCONDITION_FAIL` | `postcondition could not be proved` | result doesn't meet spec |
| `INVARIANT_ENTRY_FAIL` | `invariant.*(might not hold on entry|could not be proved.*entry)` | invariant wrong before loop |
| `INVARIANT_NOT_MAINTAINED` | `invariant.*might not be maintained` | **the headline failure mode** |
| `TERMINATION_FAIL` | `cannot prove termination|decreases` | missing/incorrect `decreases` |
| `ASSERTION_FAIL` | `assertion might not hold` | intermediate lemma wrong |
| `TIMEOUT` | subprocess `TimeoutExpired` *or* `timed out|out of resource` | prover gave up |
| `EMPTY_OR_NO_CODE` | extractor returned nothing | model didn't emit Dafny |

**Key function signatures (contract, not implementation):**
```python
def build_prompt(problem: Problem, style: PromptStyle) -> Messages: ...
def generate(model: str, messages: Messages, *, temperature: float, seed: int|None) -> str: ...  # adapter
def extract_dafny(markdown: str) -> str | None: ...
def run_dafny(code: str, *, vc_limit_s: int, wall_s: int) -> RunResult: ...      # subprocess + dual timeout
def classify(r: RunResult) -> Category: ...                                       # stdout-driven
def evaluate(problems, models, k, out_jsonl) -> None: ...                          # the loop
```

### 2.4 Benchmark suite (escalating difficulty)

Ship **P1–P4** in v1; **P5** is a stretch goal. Each problem = a `.dfy.tmpl` with a **fixed** `requires`/`ensures` and the body present but annotations stripped.

| # | Problem | Spec it must satisfy | What it isolates | Expected difficulty |
|---|---|---|---|---|
| **P1** | `Abs(x): y>=0 && (y==x || y==-x)` | post-condition only, **no loop** | sanity / smoke test | trivial — *all* pass |
| **P2** | `Max(a[]) returns m` | `m` is ≥ all elements **and** present | **one** loop invariant + array-bounds reasoning | easy |
| **P3** | `LinearSearch(a, key) returns i` | `i≥0 ⇒ a[i]==key`; `i<0 ⇒ key ∉ a` | invariant over a **∀ prefix** ("not seen yet") | medium |
| **P4** | `BinarySearch(a sorted, key)` | same post, **requires sortedness** | invariant + bounds + **`decreases` termination** + classic off-by-one | **the discriminator** |
| **P5** | `DutchNationalFlag` *or* `InsertionSort` | output is sorted **and a permutation** (multiset) | multiple coupled invariants + `multiset` preservation | hard — *expected high failure* |

P4 is where we expect the `INVARIANT_NOT_MAINTAINED` / `TERMINATION_FAIL` signal to light up — the heart of the finding.

---

## 3. Infrastructure & environment strategy (where to build)

### 3.1 The load-bearing insight

> **Verification and generation are different workloads.** Conflating them is the classic HPC mistake.

| Stage | Resource profile | Belongs on |
|---|---|---|
| **Generation** (LLM emits Dafny) | **GPU** if self-hosting open weights (vLLM); **zero local compute** if using an API | API (v1) → Aire **GPU** partition (v2 self-host) |
| **Verification** (`dafny verify` → Z3) | **CPU + RAM, single-thread per VC, no GPU**, embarrassingly parallel | Local (v1) → Aire **CPU** partition (v2 scale) |

The GPUs on Aire are **irrelevant to running the verifier.** They matter only if/when we self-host the *generators*.

### 3.2 Environment A vs B

| | **A — Local macOS** | **B — Aire HPC (Leeds)** |
|---|---|---|
| Toolchain | `brew install dafny` + VS Code Dafny ext (live verify) | Linux; Dafny via **Apptainer** image (Docker not allowed on shared HPC) |
| Scheduler | none (just run it) | **Slurm** batch + **job arrays** *(confirm partition names against Aire docs — you have direct access)* |
| GPU | no | yes — for self-hosted vLLM generation |
| Best at | dev inner-loop, debugging, the ≤10³ v1 run, plotting | thousands of (problem×model×sample) verifications; self-hosted generation at scale |
| Friction | none | queue waits, module/container setup, no interactive verifier |

### 3.3 Definitive recommendation

**Run v1 entirely on Environment A (local macOS).** Reasons: the v1 matrix is tiny and each verification is seconds; the VS Code Dafny extension gives an unbeatable debugging loop while we calibrate the classifier regexes against *real* output; and zero queue latency means the Day-1→Day-3 sprint stays tight.

**Architect v1 for portability so the lift to Aire is a config change, not a rewrite:**
- Pin the toolchain in a **container** (Docker locally ⇄ **Apptainer** on Aire) so `dafny`+`z3` versions are byte-identical across environments — this *is* the reproducibility contract.
- Keep `generate()` behind the provider-agnostic adapter so "API" ⇄ "vLLM on a GPU node" is one flag.
- Make the unit of work a single `(problem, model, sample)` JSON task so it maps cleanly onto a **Slurm job-array index** later.

### 3.4 Scale-out blueprint (when N explodes)

```
[GPU partition]  vLLM serves open-weight models ──► completions.jsonl   (generation, GPU)
                                                        │
                                                        ▼
[CPU partition]  Slurm job array: 1 array-task per line of completions.jsonl
                 each runs `apptainer exec dafny.sif dafny verify …`     (verification, CPU, parallel)
                                                        │
                                                        ▼
[login node]     reduce: cat results-*.jsonl ──► report.py ──► tables + plots   (analysis)
```
**Decision gate — promote to Aire when *any* of:** (a) total verifications ≳ a few ×10³; (b) you self-host open weights (need GPU for vLLM); (c) per-problem proof time grows (larger codebases) and a laptop core-count is the bottleneck. Until then, **HPC is premature optimisation.**

---

## 4. Frontier model selection strategy

> **Architectural principle:** the 2026 open-weight frontier moves monthly (DeepSeek V4, Qwen3-Coder-Next, GLM-5.1, MiniMax M3, Kimi K2.6 all current). Therefore the harness is **model-agnostic** and every model tag is **pinned per run** in the result row. Names below are *current best picks*, not hard-codes.

### 4.1 Task 1 — co-pilot for building the harness

**Primary: Claude Opus 4.8 (`claude-opus-4-8`) via Claude Code.** Strongest agentic coding model; multi-file edits, runs the toolchain, iterates on the classifier against live Dafny output. **Bonus:** the JD explicitly wants *"AI-natives… experience using AI-assisted programming tools (Claude Code and similar)."* Building this *with* Claude Code is itself the artefact. **Secondary: Claude Sonnet 4.6 (`claude-sonnet-4-6`)** for cheap, fast routine edits once the design is settled.

### 4.2 Task 2 — the test subjects (the models we try to break)

Design goal: a **cross-provider, reasoning-vs-non-reasoning** spread so the failure pattern is a property of *the task*, not one vendor. Minimum **two providers** for credibility.

| Role in study | Recommended model | Why this one | Runs on |
|---|---|---|---|
| **Proprietary frontier reasoner** (strong baseline — if *it* fails maintenance, that's the headline) | **Claude Opus 4.8**, extended thinking | SOTA reasoning; isolates whether deliberate CoT fixes invariant maintenance | API (v1) |
| **Open-weight reasoner** | **DeepSeek V4 / V4-Pro** | Open weights, top LiveCodeBench/1M-context, your DeepSeek lineage; self-hostable on Aire GPU | API v1 → vLLM v2 |
| **Open-weight code-instruct** (non-reasoning contrast) | **Qwen3-Coder-Next** | Code-tuned, best efficiency/param — the "fluent but not reasoning" pole of H₂ | API v1 → vLLM v2 |
| *Optional 4th* | **GLM-5.1** (current open SOTA) *or* **Llama-3.1-8B** (continuity w/ AILES; deliberately weak baseline that should fail *early*, sharpening the contrast) | breadth / cost control | either |

**The experiment, stated exactly (answering "what test are we conducting"):**
- **Independent variable:** model (optionally × prompt condition: zero-shot vs few-shot-Dafny vs reasoning-on/off).
- **Task:** annotation-infill — fixed `requires`/`ensures` + body, model restores `invariant`/`assert`/`decreases` so `dafny verify` passes. (Secondary condition: from-scratch generation, to compare — but infill is the clean measurement.)
- **Dependent variables:** `verified ∈ {0,1}`; **failure category** (§2.3 taxonomy); **pass@1** and **pass@k** over *k* samples.
- **Controls:** pinned Dafny+Z3 (container), fixed temperature, `--cores:1`, fixed seed, per-VC + wall timeouts, optional *m*-fold re-verify for stability.
- **Headline metric:** among completions that **resolve** (type-check), the **% the verifier rejects**, and within those the **share that fail specifically on invariant maintenance / termination.** That number is the neuro-symbolic story — and it maps straight onto your arXiv finding that surface metrics (BLEU/BERTScore) miss what a ground-truth judge catches.

---

## 5. Step-by-step execution plan (3-stage sprint)

A clean, reproducible **GitHub repo** is the only deliverable. Definition of done: a stranger runs `make demo` and reproduces the headline table.

### Stage 1 — DESIGN (Day 1, ~½ day)
- `brew install dafny`; confirm `dafny verify` on a hand-written `Abs`/`Max`. Pin `dafny --version` + bundled Z3 into `versions.lock`.
- **Calibrate the classifier against real stdout** — deliberately break an invariant, copy the exact message strings into the regex table. *(This is why we start local.)*
- Author `problems/P1–P4` templates (fixed spec, stripped annotations) + their reference solutions in `solutions/` (CI checks the references actually verify).
- Repo skeleton + `Dockerfile`/`Apptainer.def` pinning the toolchain.

### Stage 2 — IMPLEMENTATION (Day 2, ~1 day)
- Build `dafny_eval.py` modules §2.3; `ModelClient` adapters for ≥2 providers.
- Wire dual timeouts, JSONL store, `pass@k` + taxonomy reporter.
- Run the full v1 matrix locally (4 problems × 3 models × k=5). Save raw completions for auditability.

### Stage 3 — ANALYSIS (Day 3, ~½ day)
- Aggregate → **taxonomy bar chart** + **pass@k table**; pull 2–3 verbatim "fluent-but-unproven" failure examples (esp. P4 invariant-maintenance).
- Write the **README as the paper**: hypothesis → method → the headline number → the BLEU/BERTScore tie-in → honest limitations. Tag a `v0.1` release.

**Repo layout:**
```
dafny-eval/
├── README.md                # the writeup = the deliverable
├── Makefile                 # `make demo`, `make verify-refs`
├── versions.lock            # pinned dafny + z3
├── Dockerfile / Apptainer.def
├── dafny_eval.py            # CLI entry
├── dafny_eval/              # loader · client · extractor · runner · classifier · report
├── problems/                # P1–P4(.5) .dfy.tmpl  (fixed spec, holes)
├── solutions/               # reference verified .dfy (CI-checked)
├── results/                 # results.jsonl + plots  (committed for repro)
└── .github/workflows/ci.yml # verify references on every push
```

---

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Spec-gaming** (model weakens `ensures`) | Annotation-infill: *we* own the spec |
| **Verifier non-determinism** | Pinned Z3 + `--cores:1` + seed + optional *m*-fold stability check |
| **Exit-code reliance breaks silently** | Classify by stdout; exit code is coarse backstop only |
| **Dated model tags** | Model-agnostic client; tags pinned per run in JSONL |
| **HPC over-engineering** | Local-first; Aire gated behind explicit §3.4 thresholds |
| **Over-claiming on CV** | Claim *the experiment you ran*, never "contributed to formal verification." The repo speaks for itself. |

---

## 7. ⛳ Open decision — confirm before we write code

My definitive recommendation is **Environment A (local macOS) for v1**, architected so the jump to **Aire (Slurm CPU job-arrays + optional GPU/vLLM generation)** is a config change when §3.4 thresholds hit.

**Please confirm one:**
- **(A) Local macOS** — I scaffold the repo + `dafny_eval.py` targeting your laptop toolchain immediately *(recommended)*.
- **(B) Aire HPC now** — I generate the Apptainer def + Slurm array templates first and we build for the cluster from the start.

Tell me **A or B** and I'll start writing the code for that environment.

---

### References
- Dafny Reference Manual — CLI, verification, exit-code note · dafny.org/latest/DafnyRef/DafnyRef
- "Dafny 4 is released" (Z3 4.12.1 default) · dafny.org/blog/2023/03/03/dafny-4-released
- DafnyBench: A Benchmark for Formal Software Verification — Loughridge et al. · namin.seas.harvard.edu/pubs/dafnybench.pdf
- Clover: Closed-Loop Verifiable Code Generation · arXiv:2310.17807
- DafnyPro: LLM-Assisted Automated Verification for Dafny · arXiv:2601.05385
- 2026 open-weight landscape (DeepSeek V4 / Qwen3-Coder-Next / GLM-5.1) · LiveBench / SWE-bench snapshots
