# PIPELINE_AUDIT.md
### Adversarial audit — dafny-eval, as it stands at 315-run master matrix
**Reviewer stance:** Principal ML Benchmarking Reviewer + Formal-Methods Auditor. Unvarnished.

> **One-line verdict:** This is a *methodologically strong per-item instrument with a low ceiling.*
> It rigorously measures one thing — single-loop invariant **infill** — and frontier models have
> already beaten it 105/105. As a frontier benchmark it is **saturated and effectively complete**;
> its durable asset is the **hardened harness**, not the problem suite. The current scoring
> architecture also **cannot represent** the very problems (multi-declaration / compositional)
> needed to break that ceiling — see §2 E6.

---

## 1. End-to-end pipeline trace (actual code path)

Tracing one cell — `(p6_max_profit, claude-haiku-4-5, prompt=base, sample=0)`:

1. **Intake — `load_problems()`** globs `solutions/*.dfy`. **There are no `.dfy.tmpl` files** — the
   spec/implementation diverged here: each `solutions/pN_*.dfy` *is* the verified reference, and the
   "template" is generated at runtime. Flagging this because the project's own docs still say
   `.dfy.tmpl`.
2. **Template synthesis — `strip_annotations(ref)`** (line 79) drops every line matching
   `^\s*(invariant|decreases|assert)\b`. That is the holed skeleton the model sees. *Line-based* — see
   §2 E for why that bites.
3. **Prompt — `prompt_variants(stripped)`** (161) → `{base, versioned, fewshot}`; `build_user` wraps
   the skeleton in a ```` ```dafny ```` fence. System prompt is `SYSTEM_BASE` (+ version line / few-shot).
4. **Generate — `generate()` → `_gen_anthropic()`** (194): POST `/v1/messages`, `thinking:{adaptive}`
   unless `--no-thinking`, returns `("".join(text blocks), stop_reason)`. **Sampling is unseeded.**
5. **Extract — `extract_dafny(text)`** (240): first ```` ```dafny ```` block → else longest fence → else
   whole text if it contains `method`/`function`. Returns `None` otherwise.
6. **The gate — `score(code, ref)`** (the heart):
   a. truncation/no-code → `TRUNCATED`/`NO_CODE`;
   b. `denylist_hit(code)` (comment-stripped) → `UNSOUND`;
   c. `split_method(code)` → `model_body`; `split_method(ref)` → `(ref_header, ref_body)`;
   d. **stitch** = `ref_header + "{" + model_body + "}"` — *our* signature+`requires`+`ensures` over
      *their* body;
   e. `run_dafny(stitched)`; `classify(run)`; truncation override; `body_changed` analytics flag.
7. **Verify — `run_dafny()`** (274): temp dir, write `.dfy`, `subprocess.run(["dafny","verify",
   "--cores:1","--resource-limit:10M","--log-format","csv;…"], timeout=90)`; parse `ResourceCount`
   from the CSV; wall-timeout is a backstop only.
8. **Classify — `classify(run)`** (311): ordered string search over `stdout+stderr` — `parse errors
   detected` → `resolution/type errors detected` → SUCCESS (`verifier finished with [1-9]\d* verified,
   0 errors` **and** `returncode==0`) → `_VERIFY_CHECKS` (entry, maintain, termination, bounds,
   postcond, precond, assertion) → `OTHER_VERIFICATION_FAIL`.
9. **Log — `_row()` → JSONL**, written incrementally; `report()` aggregates with Wilson CIs.

The dataflow is sound and I confirm it works as described. The fragilities are in steps 5, 6c–d, and 8.

---

## 2. Standards & gap analysis

### 2a. Versus the gold standard

| Axis | HumanEval / MBPP | DafnyBench | **dafny-eval (ours)** |
|---|---|---|---|
| Items | 164 / ~974 | ~750 | **7** |
| Structural diversity | high | high (harvested real code) | **1 family** (single-loop array methods) |
| Task | NL→code, execution-tested | fill annotations, verify | fill annotations, verify (**same as DafnyBench**) |
| Soundness gate | n/a (tests) | ground-truth spec kept | **semantic stitch + denylist** (stronger) |
| Statistics | pass@k, big n | % verified | **Wilson CIs, k=5** |
| Contamination | partial decontam analyses | *higher* risk (real Dafny in training) | **asserted, not measured** |
| Ablations | — | — | **prompt ensemble + capability ladder** (ahead) |

**Honest read:** we are *ahead* of all three on per-item rigor (soundness stitch, CIs, ablation,
deterministic budgeting) and *two orders of magnitude behind* DafnyBench on scale and diversity, with
**no contamination measurement** (HumanEval-era benchmarks now ship n-gram/canary decontam; we ship an
argument). We are a soundness-hardened 7-item probe, not a benchmark.

### 2b. Statistical / methodological blind spots

- **n=5 per cell.** Aggregated (n=15) cells give `[0.80, 1.00]` lower bounds — usable. Per-prompt n=5
  cells (`0.20 [0.04, 0.62]`) are individually uninformative; the §3 prompt *ranking* is within noise.
- **63 cells, no multiple-comparison control.** At α=0.05 you expect ~3 spurious "significant" cells;
  we never ran significance tests, so this is latent rather than committed — but any future per-cell
  claim needs family-wise correction.
- **No unbiased pass@k.** We report verify-rate `c/5`, not the HumanEval pass@k estimator (which needs
  n≫k). Defensible for a reliability framing, but not the field-standard metric.
- **No baselines/anchors.** Oracle = 100% by construction; stripped = 0% (a floor); there is no
  no-op/random floor and no human ceiling, so "100%" floats without a scale.
- **Contamination unmeasured** (2a). "Gap ≈ 0" between canonical and novel is also confounded by the
  *ceiling* — you cannot see a gap when both arms are pinned at 1.0.

### 2c. Exploitable / fragile edge cases — the semantic-integrity gate

- **E1 · `split_method` is brace-counting, not lexing (lines 94–107), and runs on *raw* code (comments
  included).** A model comment containing an unbalanced brace — `// close the block } here` — decrements
  the depth counter and closes the method early, truncating `model_body`. String/char literals (`"}"`,
  `'{'`) do the same. Result: a malformed stitch → spurious `PARSE`/`RESOLUTION` (a **false negative**,
  under-counting correct solutions). Not a pass-exploit (a truncated body won't verify our spec), but a
  real robustness bug. Fix: strip comments before `split_method`, or lex.
- **E6 · The gate is hard-wired to a SINGLE top-level method — and this is the load-bearing flaw.**
  `split_method(reference)` takes everything before the *first* `{` as the header. The moment a problem
  needs a helper `function`/`lemma` (i.e., every harder proof — Kadane's `SumTo`, any compositional
  task), the first `{` is the *helper's* body, the stitch is garbage, and the gate breaks. **The
  architecture cannot represent the problems required to escape saturation (§3).** This is the single
  most important finding in this audit: *the instrument and its only worthwhile extension are
  architecturally incompatible as written.*
- **E2 · The denylist is a blocklist, not an allowlist.** It catches six known escape hatches
  (`assume`, `{:axiom}`, `{:verify false}`, `decreases *`, `assert false`, `{:extern}`). It cannot catch
  an *unknown* bypass or a new Dafny attribute. Beyond it, only Dafny's own soundness and
  warnings-as-errors protect us. Principled fix is an allowlist or AST-level analysis.
- **E3 · Warnings-as-errors over-rejects (the inverse risk).** We run *without* `--allow-warnings` as a
  second line of defense, so **any** warning (unused var, deprecation) on an otherwise-correct proof
  → non-zero exit → not SUCCESS → `OTHER_VERIFICATION_FAIL`. That is a **false negative**: a verifying
  solution scored as a failure. We never measured how often this fires.
- **E7 · Sampling is unseeded; "no-thinking" is still non-deterministic.** Determinism covers
  *verification* (`--resource-limit`, `RandomSeed=0`, pinned Z3) but not *generation* — a specific run is
  not reproducible.

### 2d. Exploitable / fragile edge cases — the regex taxonomy

- **E4 · `classify()` searches the whole `stdout+stderr` blob, which includes echoed source lines.**
  Dafny quotes the offending source in error messages, so the model's own text appears in the string we
  classify. A model can therefore **steer its own failure category** by planting a trigger phrase in a
  comment/identifier (`// index out of range`). It *cannot* forge SUCCESS — that needs Dafny's exact
  summary line **and** `returncode==0`, neither model-controlled — but it can corrupt the **taxonomy
  distribution**, which is increasingly the instrument's main product. Fix: classify only Dafny's
  structured `file.dfy(line,col): Error:` lines, not arbitrary stdout.
- **E5 · First-match ordering biases the taxonomy.** A multi-error program is attributed to the
  highest-priority regex (entry > maintain > termination > bounds > postcond …). This is a documented
  choice, but it means the failure histogram is partly an artifact of our ordering, not a neutral
  measurement. Multi-label reporting would be more honest.
- **Single verifier / single Z3, pinned.** Reproducible, but every result is Dafny-4.11-scoped; we have
  no cross-version or cross-tool (Verus/F*/Lean) generalization.

**Net:** none of these let a model turn a fail into a **pass** (SUCCESS is well-protected by the stitch
+ `returncode==0` + Dafny owning the summary line). They cause **false negatives** (E1, E3) and
**taxonomy corruption** (E4, E5). For an instrument now valued for its failure taxonomy, E4 is the one I
would fix first.

---

## 3. Strategic extension planning (ranked by ROI)

The binding constraint is the **Oracle Limit**: the proofs hard enough to move frontier models off 1.0
are exactly the ones whose *reference* proofs we cannot author/verify cheaply. Every extension must be
judged on whether it restores dynamic range *without* requiring un-authorable references.

**#1 — Compositional reasoning (multi-function chains). Highest ROI.** Each helper is individually
trivial to verify (references are cheap and authorable), but the *composition* — aligning pre/post
across function boundaries, threading ghost state, calling the right lemma — is exactly where frontier
models are reported to collapse. It escapes the Oracle Limit (cheap pieces) **and** restores dynamic
range (hard glue) **and** raises external validity (real multi-function code). **Caveat (from E6):** the
current single-method gate must be re-architected first — stitch/verify a multi-declaration file with
canonical specs per function. That gate work *is* the project's next real engineering, and it is worth
more than any number of additional runs on the current suite.

**#2 — Agentic self-healing (multi-turn repair). High ROI, directly product-aligned.** Feed Dafny's
specific error (`invariant could not be proved to be maintained`) back and let the model repair over N
turns. This measures a *different and more important* capability — *using verifier feedback*, which is
literally Reasonable AI's "formal oversight in the loop" — and it can discriminate even where zero-shot
saturates (turns-to-converge, repair-success rate). We are unusually well-positioned: our classifier
already extracts the exact failure category + error snippet, i.e., the feedback signal is built. Cost:
a multi-turn harness.

**#3 — Negative controls (unsatisfiable specs). Cheap; do it for defensibility, not discovery.** Add
problems whose spec is well-formed but impossible; the *correct* outcome is FAIL. This proves the
instrument can emit a true negative and that the stitch+denylist make "verifying" an impossible spec
impossible (a pass there would expose a soundness bug in Dafny or our gate). High rigor-per-effort, but
low new information — we already have failures.

**#4 — Open-weight moat validation. LOW ROI *now*; gate it behind #1.** Brutal prediction: our suite
saturates even Haiku on 6/7, so DeepSeek-V4 / Qwen3-Coder / GLM / Kimi will almost certainly *also*
saturate 6/7 → another all-green table. Running it on the **current** suite mostly reconfirms that this
tier is an industry commodity (mildly interesting) and **cannot find a moat because there is no
discriminating signal left to find.** It becomes high-value *only* on a discriminating suite
(compositional). Do it after #1, not before.

**Problem domains (if staying single-method):** ranked by discrimination-per-authoring-cost —
**termination metrics** (lexicographic/mutual `decreases`; we have *zero* real `TERMINATION` failures, so
it's untested and authorable) > **ghost-state threading** > **datatype/sequence problems with
quantifier-heavy invariants** (harder for models than array loops). But be honest: single-method tweaks
will likely **also saturate**. The domain that matters is **compositionality**, not another single
method.

**Bottom line on spend:** the next dollar goes to **gate generalization → compositional problems**, with
**agentic self-healing** as the parallel high-value axis. More models / prompts / samples on the current
7-item suite is **wasted budget** — it re-measures a ceiling we already know.

---

## 4. Data-visualization plan (for the README / a paper)

Data dimensions: 3 models × 7 problems × 3 prompts × k=5, with pass-rate + Wilson CI + 14-way failure
taxonomy. For an elite audience, **error bars are non-negotiable and are themselves the credibility
signal** — the charts should *advertise* the uncertainty, not hide it.

**Chart 1 (headline) — grouped bar, pass-rate by problem × model, with Wilson-95% error bars.**
x = P1–P7, three bars/group (Haiku/Sonnet/Opus), y ∈ [0,1], aggregated n=15. Communicates the entire
result in one frame: six problems pinned at 1.0 and **P6 as the lone discriminator** with the clean
Haiku 0.47 < Sonnet 0.80 < Opus 1.0 gradient. The visible `[0.80,1.0]` bands set up the saturation
argument honestly.

**Chart 2 (the finding) — P6-only grouped bar, model × prompt, with error bars.** x = {base, versioned,
fewshot}, bars = models. This is the *counterintuitive* story: non-monotonic, prompt-fragile, Opus flat
at 1.0 while Haiku *degrades* with "better" prompts. Annotate the failure-mode shift
(base→`RESOLUTION`, versioned/fewshot→`INVARIANT_NOT_MAINTAINED`). This is the chart that earns a
double-take.

**Chart 3 (optional, with a caveat) — failure-mode composition.** A segmented/stacked bar of the 11
non-success runs by category, or better a small base→richer "shift" diagram. **Honest caveat: n=11 is
too sparse for a quantitative chart** — present it as an annotated table or a qualitative shift, not as
if the proportions are estimated. Do not pie-chart 11 points.

**Anti-patterns to refuse:** a 63-cell heatmap (mostly green → low information density, hides the one
result); any bar without CIs; a giant "100%" with no interval; pass@k curves we don't have the n to
support. Plot the **lower bound**, not just the point estimate.

---

## 5. The ceiling — unvarnished

This instrument is a **validated measuring tape that is now too short for what we want to measure.** Per
item it is genuinely rigorous — soundness-stitched, contamination-aware-by-design, CI-backed, with a
flaw it found and fixed in its own scorer. But:

- Its **scientific reach for frontier models is exhausted.** 6/7 problems are at 1.0 across the board;
  Opus is 105/105 `[0.96, 1.00]`. More samples, models, or prompts on this suite re-measure a known
  ceiling.
- Its **only worthwhile extension (compositional / lemma-bearing problems) is incompatible with its
  current single-method gate** (E6). The headline next step is therefore *architectural*, not
  experimental.
- Its **remaining scientific value is a negative result** ("current frontier models reliably synthesize
  single-loop invariants under a cheat-proof gate") plus a **sub-frontier discriminator** (P6) plus a
  **reusable hardened harness** — which is the real asset, *if* it is extended to compositional and
  agentic settings.

**Verdict:** as a *benchmark of frontier capability*, declare it **complete and saturated** — ship it as
a rigorous negative result, not as an open leaderboard. As a *platform*, its value is entirely in front
of it, and entirely gated on the gate rewrite (§3 #1) and the multi-turn harness (§3 #2). Do not spend
another dollar widening the current matrix; spend it making the instrument able to measure something the
frontier has not already beaten.
