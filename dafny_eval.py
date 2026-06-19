#!/usr/bin/env python3
"""
dafny_eval.py — Can frontier models write *verifiable* code?

Annotation-infill benchmark: strip the loop invariants / decreases / asserts from a verified
Dafny method, ask an LLM to restore them, and score the result through a strict gate.

Scoring gate (Tier 0 + semantic integrity)
-------------------------------------------
  1. TRUNCATED  — API stop_reason == max_tokens/length and output unusable.
  2. UNSOUND    — verifier escape hatch (assume, {:axiom}, {:verify false},
                  decreases *, assert false, {:extern}).
  3. SEMANTIC INTEGRITY (not textual): we extract the model's method BODY, re-inject our
     canonical signature + requires + ensures, and run `dafny verify` on the stitched
     method. A pass means the body+invariants prove OUR spec — the model cannot benefit
     from weakening it, and benign edits (renamed locals, reformatting) no longer fail.
     `body_changed` is logged for analytics; it never fails the run.
  4. classify() the stitched verification.

Stdlib only. Classifier strings calibrated against Dafny 4.11.0. `dafny verify` runs WITHOUT
--allow-warnings (warnings stay hard errors — a second line of defence behind the denylist).

Usage
-----
  python3 dafny_eval.py calibrate | selftest | guardtest
  python3 dafny_eval.py run --models anthropic:claude-opus-4-8 --k 5 \
          [--prompts base versioned fewshot] [--no-thinking]
  python3 dafny_eval.py rescore results/results.jsonl
  python3 dafny_eval.py report  results/results.jsonl
"""
from __future__ import annotations

import argparse, csv, glob, json, math, os, re, ssl, subprocess, sys, tempfile, time
import urllib.request, urllib.error
from collections import Counter, defaultdict
from pathlib import Path

ROOT          = Path(__file__).resolve().parent
SOLUTIONS_DIR = ROOT / "solutions"
CALIB_DIR     = ROOT / "tests" / "calibration"
RESULTS_DIR   = ROOT / "results"
DAFNY_BIN     = os.environ.get("DAFNY_BIN", "dafny")
DAFNY_VERSION = "4.11.0"

DEFAULT_RLIMIT = 10_000_000
DEFAULT_WALL_S = 90
DEFAULT_MAXTOK = 8192


# --------------------------------------------------------------------------- #
# Result categories
# --------------------------------------------------------------------------- #
class Cat:
    SUCCESS              = "FULL_SUCCESS"
    PARSE                = "PARSE_ERROR"
    RESOLUTION           = "RESOLUTION_ERROR"
    PRECOND              = "PRECONDITION_FAIL"
    POSTCOND             = "POSTCONDITION_FAIL"
    INV_ENTRY            = "INVARIANT_ENTRY_FAIL"
    INV_MAINTAIN         = "INVARIANT_NOT_MAINTAINED"
    TERMINATION          = "TERMINATION_FAIL"
    WELLFORMEDNESS_BOUNDS = "WELLFORMEDNESS_BOUNDS"   # array bounds / subset / div-by-zero
    ASSERTION            = "ASSERTION_FAIL"
    OTHER_VERIFY         = "OTHER_VERIFICATION_FAIL"
    TIMEOUT              = "TIMEOUT"
    NO_CODE              = "EMPTY_OR_NO_CODE"
    TRUNCATED            = "TRUNCATED"
    UNSOUND              = "UNSOUND"

_VERIFY_REACHED = {Cat.SUCCESS, Cat.PRECOND, Cat.POSTCOND, Cat.INV_ENTRY, Cat.INV_MAINTAIN,
                   Cat.TERMINATION, Cat.WELLFORMEDNESS_BOUNDS, Cat.ASSERTION, Cat.OTHER_VERIFY}


# --------------------------------------------------------------------------- #
# Text utilities
# --------------------------------------------------------------------------- #
_STRIP_RE = re.compile(r"^\s*(invariant|decreases|assert)\b")

def strip_annotations(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not _STRIP_RE.match(l)) + "\n"

def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", " ", src)
    return src

def _canon(src: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(src))

_DECL_KW = re.compile(r"\b(function|predicate|method|lemma)\b")

def split_declarations(src: str):
    """Comment-stripped, brace-matched split into top-level declarations. Returns
    [(name, header, body), ...] for function/predicate/method/lemma. header = keyword .. body brace;
    body = text inside the outermost braces. A single-method file yields one element (so the
    original single-loop suite is unchanged). Comment-stripping first also removes the audit's
    brace-in-comment hazard. String literals are not lexed — fine for this suite (no string braces)."""
    s = _strip_comments(src)
    decls, i, n = [], 0, len(s)
    while i < n:
        m = _DECL_KW.search(s, i)
        if not m:
            break
        j = m.end()                            # body-opening '{', skipping '{:' attributes
        while True:
            j = s.find("{", j)
            if j < 0 or not (j + 1 < n and s[j + 1] == ":"):
                break
            j += 1
        if j < 0:
            break
        nm = re.search(r"[A-Za-z_]\w*", s[m.end():j])
        name = nm.group(0) if nm else f"?{len(decls)}"
        depth, body_start, k = 0, None, j
        while k < n:
            if s[k] == "{":
                depth += 1
                if depth == 1:
                    body_start = k + 1
            elif s[k] == "}":
                depth -= 1
                if depth == 0:
                    decls.append((name, s[m.start():j], s[body_start:k]))
                    break
            k += 1
        i = k + 1
    return decls


# --------------------------------------------------------------------------- #
# Soundness denylist
# --------------------------------------------------------------------------- #
_DENY = [
    (re.compile(r"\bassume\b"),                "assume"),
    (re.compile(r"\{\s*:\s*axiom\b"),          "{:axiom}"),
    (re.compile(r"\{\s*:\s*verify\s+false\b"), "{:verify false}"),
    (re.compile(r"\bdecreases\s*\*"),          "decreases *"),
    (re.compile(r"\bassert\s+false\b"),        "assert false"),
    (re.compile(r"\{\s*:\s*extern\b"),         "{:extern}"),
]

def denylist_hit(code: str):
    scrubbed = _strip_comments(code)
    for pat, name in _DENY:
        if pat.search(scrubbed):
            return name
    return None


# --------------------------------------------------------------------------- #
# Prompt ensemble
# --------------------------------------------------------------------------- #
SYSTEM_BASE = (
    "You are an expert in the Dafny verification language. You are given a Dafny method whose "
    "specification (requires/ensures) and body are fixed and correct, but whose loop invariants, "
    "decreases clauses, and helper assertions have been removed. Add back the MINIMAL annotations "
    "needed so that `dafny verify` proves the method with zero errors. Do NOT change the signature, "
    "requires, ensures, or the algorithm, and do NOT use assume/{:axiom}/{:verify false}/decreases */"
    "assert false/{:extern}. Return ONLY one ```dafny code block with the complete method."
)

DAFNY_VER_LINE = (
    f" Target Dafny {DAFNY_VERSION}: use only standard Dafny syntax — quantifiers are written "
    "`forall x :: P` / `exists x :: P` (never English like 'for all i in ...'), and there is no "
    "built-in `min`/`max` function."
)

FEWSHOT = (
    "Here is a worked example of the task (a different problem).\n\nGiven:\n```dafny\n"
    "method Triple(x: int) returns (r: int)\n  ensures r == 3 * x\n{\n  var i := 0;\n  r := 0;\n"
    "  while i < 3\n  {\n    r := r + x;\n    i := i + 1;\n  }\n}\n```\nCorrect completion:\n```dafny\n"
    "method Triple(x: int) returns (r: int)\n  ensures r == 3 * x\n{\n  var i := 0;\n  r := 0;\n"
    "  while i < 3\n    invariant 0 <= i <= 3\n    invariant r == i * x\n    decreases 3 - i\n"
    "  {\n    r := r + x;\n    i := i + 1;\n  }\n}\n```\n"
)

def build_user(stripped: str) -> str:
    return ("Restore the verification annotations so this verifies:\n\n```dafny\n"
            + stripped.strip() + "\n```")

def prompt_variants(stripped: str):
    """name -> (system, user). 'base' is the original zero-shot control; the others isolate the
    version-skew and zero-shot-fluency confounds."""
    user = build_user(stripped)
    return {
        "base":      (SYSTEM_BASE, user),
        "versioned": (SYSTEM_BASE + DAFNY_VER_LINE, user),
        "fewshot":   (SYSTEM_BASE + DAFNY_VER_LINE, FEWSHOT + "\n" + user),
    }
PROMPT_NAMES = ["base", "versioned", "fewshot"]


# --------------------------------------------------------------------------- #
# Model adapters — each returns (text, stop_reason)
# --------------------------------------------------------------------------- #
def _http_json(url, headers, payload, timeout=180, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 529):
                raise
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise last

def _gen_anthropic(model, system, user, max_tokens=DEFAULT_MAXTOK, thinking=True):
    payload = {"model": model, "max_tokens": max_tokens,
               "system": system, "messages": [{"role": "user", "content": user}]}
    if thinking:
        payload["thinking"] = {"type": "adaptive"}
    data = _http_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
         "anthropic-version": "2023-06-01", "content-type": "application/json"}, payload)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return text, data.get("stop_reason")

def _gen_openai(model, system, user, base_url, api_key, max_tokens=DEFAULT_MAXTOK):
    data = _http_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        {"model": model, "max_tokens": max_tokens,
         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    ch = data["choices"][0]
    return ch["message"]["content"], ch.get("finish_reason")

def generate(model_spec, system, user, oracle_src=None, thinking=True):
    if model_spec == "oracle":
        return "```dafny\n" + (oracle_src or "") + "\n```", "end_turn"
    provider, _, rest = model_spec.partition(":")
    if provider == "anthropic":
        return _gen_anthropic(rest, system, user, thinking=thinking)
    if provider == "openai":
        return _gen_openai(rest, system, user,
                           os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                           os.environ["OPENAI_API_KEY"])
    if provider == "deepseek":
        return _gen_openai(rest, system, user,
                           os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                           os.environ["DEEPSEEK_API_KEY"])
    if provider == "vllm":
        model, _, base = rest.partition("@")
        return _gen_openai(model, system, user, base or "http://localhost:8000/v1",
                           os.environ.get("VLLM_API_KEY", "EMPTY"))
    raise ValueError(f"unknown model spec: {model_spec!r}")


# --------------------------------------------------------------------------- #
# Multi-turn chat (for agentic self-healing)
# --------------------------------------------------------------------------- #
def generate_chat(model_spec, system, messages, thinking=False):
    """messages: list of {role, content}. Returns (text, stop_reason). thinking defaults OFF to
    sidestep multi-turn thinking-block replay rules."""
    provider, _, rest = model_spec.partition(":")
    if provider == "anthropic":
        payload = {"model": rest, "max_tokens": DEFAULT_MAXTOK, "system": system, "messages": messages}
        if thinking:
            payload["thinking"] = {"type": "adaptive"}
        data = _http_json("https://api.anthropic.com/v1/messages",
                          {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                           "anthropic-version": "2023-06-01", "content-type": "application/json"}, payload)
        return ("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"),
                data.get("stop_reason"))
    if provider == "openai":
        base, api, model = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), os.environ["OPENAI_API_KEY"], rest
    elif provider == "deepseek":
        base, api, model = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), os.environ["DEEPSEEK_API_KEY"], rest
    elif provider == "vllm":
        model, _, b = rest.partition("@"); base, api = b or "http://localhost:8000/v1", os.environ.get("VLLM_API_KEY", "EMPTY")
    else:
        raise ValueError(f"unknown model spec: {model_spec!r}")
    data = _http_json(f"{base.rstrip('/')}/chat/completions",
                      {"Authorization": f"Bearer {api}", "content-type": "application/json"},
                      {"model": model, "max_tokens": DEFAULT_MAXTOK,
                       "messages": [{"role": "system", "content": system}] + messages})
    ch = data["choices"][0]
    return ch["message"]["content"], ch.get("finish_reason")


# --------------------------------------------------------------------------- #
# Extract a Dafny code block
# --------------------------------------------------------------------------- #
_FENCE_DAFNY = re.compile(r"```dafny\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FENCE_ANY   = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.DOTALL)

def extract_dafny(text):
    if not text:
        return None
    m = _FENCE_DAFNY.search(text)
    if m:
        return m.group(1).strip()
    blocks = _FENCE_ANY.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    if "method " in text or "function " in text:
        return text.strip()
    return None


# --------------------------------------------------------------------------- #
# Verifier (deterministic resource budget + per-proof resource logging)
# --------------------------------------------------------------------------- #
def _s(x):
    return "" if x is None else (x if isinstance(x, str) else x.decode("utf-8", "replace"))

def _parse_rlimit(log_path):
    try:
        with open(log_path, newline="") as fh:
            rows = list(csv.reader(fh))
        total, seen = 0, False
        for r in rows[1:]:
            if len(r) > 3 and r[3].isdigit():
                total += int(r[3]); seen = True
        return total if seen else None
    except Exception:
        return None

def run_dafny(code, rlimit=DEFAULT_RLIMIT, wall_s=DEFAULT_WALL_S):
    with tempfile.TemporaryDirectory() as d:
        f, log = Path(d) / "candidate.dfy", Path(d) / "log.csv"
        f.write_text(code)
        cmd = [DAFNY_BIN, "verify", "--cores:1", f"--resource-limit:{rlimit}",
               "--log-format", f"csv;LogFileName={log}", str(f)]
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall_s)
            out, err, rc, to = p.stdout, p.stderr, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out, err, rc, to = _s(e.stdout), _s(e.stderr), None, True
        return {"stdout": out, "stderr": err, "returncode": rc,
                "wall_ms": int((time.monotonic() - t0) * 1000),
                "timed_out": to, "resource_count": _parse_rlimit(log)}


# --------------------------------------------------------------------------- #
# Classify (CALIBRATED against Dafny 4.11.0)
# --------------------------------------------------------------------------- #
_VERIFY_CHECKS = [
    (Cat.INV_ENTRY,             r"loop invariant could not be proved on entry"),
    (Cat.INV_MAINTAIN,          r"invariant could not be proved to be maintained"),
    (Cat.TERMINATION,           r"cannot prove termination|decreases expression might not decrease"),
    (Cat.WELLFORMEDNESS_BOUNDS, r"index out of range|possible array bounds violation|"
                                r"value does not satisfy the subset constraints|possible division by zero"),
    (Cat.POSTCOND,              r"postcondition could not be proved"),
    (Cat.PRECOND,               r"precondition.*could not be proved|could not be proved.*precondition"),
    (Cat.ASSERTION,             r"assertion (might not hold|could not be proved)"),
]

def _first_error(out):
    for line in out.splitlines():
        if "Error:" in line:
            return line.strip()[:200]
    return ""

def classify(run):
    out = _s(run.get("stdout")) + "\n" + _s(run.get("stderr"))
    if run.get("timed_out"):
        return Cat.TIMEOUT, "wall-clock timeout"
    if "parse errors detected" in out:
        return Cat.PARSE, _first_error(out)
    if "resolution/type errors detected" in out:
        return Cat.RESOLUTION, _first_error(out)
    if re.search(r"verifier finished with [1-9]\d* verified, 0 errors", out) and run.get("returncode") == 0:
        return Cat.SUCCESS, ""
    for cat, pat in _VERIFY_CHECKS:
        if re.search(pat, out):
            return cat, _first_error(out)
    return Cat.OTHER_VERIFY, _first_error(out)


# --------------------------------------------------------------------------- #
# Wilson score interval (95%)
# --------------------------------------------------------------------------- #
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# --------------------------------------------------------------------------- #
# THE GATE: semantic integrity (Tier 0 + spec re-injection)
# --------------------------------------------------------------------------- #
def score(code, reference, *, stop_reason=None, rlimit=DEFAULT_RLIMIT, wall_s=DEFAULT_WALL_S):
    """Return (category, snippet, run_or_None, meta). meta = {'body_changed': bool}."""
    truncated = stop_reason in ("max_tokens", "length")
    if not code:
        return (Cat.TRUNCATED if truncated else Cat.NO_CODE,
                "truncated: no code" if truncated else "no code block", None, {})
    hit = denylist_hit(code)
    if hit:
        return Cat.UNSOUND, f"unsound construct: {hit}", None, {}
    # SEMANTIC INTEGRITY (multi-declaration): for EACH canonical declaration, force our
    # signature + requires/ensures and keep the model's body, then verify the reassembled
    # program. A single-method file is the 1-declaration special case.
    ref_decls = split_declarations(reference)
    model_decls = {name: (h, b) for (name, h, b) in split_declarations(code)}
    if not ref_decls:
        return Cat.OTHER_VERIFY, "reference has no parseable declaration", None, {}
    parts, changed = [], False
    for name, ref_h, ref_b in ref_decls:
        if name not in model_decls:
            return (Cat.TRUNCATED if truncated else Cat.NO_CODE,
                    f"missing declaration: {name}", None, {})
        _, model_b = model_decls[name]
        parts.append(ref_h.rstrip() + "\n{\n" + model_b.strip() + "\n}\n")
        if _canon(strip_annotations(model_b)) != _canon(strip_annotations(ref_b)):
            changed = True
    run = run_dafny("\n\n".join(parts), rlimit=rlimit, wall_s=wall_s)
    cat, snip = classify(run)
    if cat != Cat.SUCCESS and truncated:
        cat, snip = Cat.TRUNCATED, "truncated before a clean proof"
    return cat, snip, run, {"body_changed": changed}


# --------------------------------------------------------------------------- #
# Evaluation loop
# --------------------------------------------------------------------------- #
def load_problems(names=None):
    probs = {}
    for p in sorted(SOLUTIONS_DIR.glob("*.dfy")):
        if names and p.stem not in names:
            continue
        probs[p.stem] = p.read_text()
    return probs

def _row(pname, model, prompt, s, raw, code, run, cat, snip, stop_reason=None, body_changed=None):
    run = run or {}
    return {"problem": pname, "model": model, "prompt": prompt, "sample": s,
            "dafny": DAFNY_VERSION, "verified": cat == Cat.SUCCESS, "category": cat,
            "body_changed": body_changed, "stop_reason": stop_reason,
            "returncode": run.get("returncode"), "wall_ms": run.get("wall_ms"),
            "resource_count": run.get("resource_count"), "timed_out": run.get("timed_out", False),
            "error_snippet": snip, "extracted_code": code, "completion_raw": raw}

def evaluate(models, k, out_path, problem_names=None, rlimit=DEFAULT_RLIMIT,
             wall_s=DEFAULT_WALL_S, thinking=True, prompt_names=None, sample_start=0):
    prompt_names = prompt_names or ["base"]
    probs = load_problems(problem_names)
    RESULTS_DIR.mkdir(exist_ok=True)
    rows = []
    with open(out_path, "w") as fout:
        for pname, ref in probs.items():
            variants = prompt_variants(strip_annotations(ref))
            for model in models:
                for pn in prompt_names:
                    system, user = variants[pn]
                    for s in range(sample_start, sample_start + k):
                        try:
                            raw, stop = generate(model, system, user, oracle_src=ref, thinking=thinking)
                        except Exception as e:                       # noqa: BLE001
                            row = _row(pname, model, pn, s, "", None, None, Cat.NO_CODE,
                                       f"gen_error: {e}", stop_reason="error")
                            fout.write(json.dumps(row) + "\n"); rows.append(row)
                            print(f"  {pname:16s} {model:24s} {pn:9s} #{s} -> GEN_ERROR {e}")
                            continue
                        code = extract_dafny(raw)
                        cat, snip, run, meta = score(code, ref, stop_reason=stop, rlimit=rlimit, wall_s=wall_s)
                        row = _row(pname, model, pn, s, raw, code, run, cat, snip,
                                   stop_reason=stop, body_changed=meta.get("body_changed"))
                        fout.write(json.dumps(row) + "\n"); rows.append(row)
                        print(f"  {pname:16s} {model:24s} {pn:9s} #{s} -> {cat}")
    return rows


# --------------------------------------------------------------------------- #
# Reporting (Wilson CIs)
# --------------------------------------------------------------------------- #
def _short(m):
    return m.split(":")[-1].replace("claude-", "")

def report(path):
    rows = [json.loads(l) for l in open(path)]
    agg = defaultdict(list); cats = Counter()
    multi_prompt = len({r.get("prompt", "base") for r in rows}) > 1
    for r in rows:
        key = (r["problem"], r["model"], r.get("prompt", "base"))
        agg[key].append(r); cats[r["category"]] += 1
    print("\n=== pass rate (95% Wilson CI) ===")
    hdr = f"{'problem':16s}{'model':18s}"
    if multi_prompt: hdr += f"{'prompt':10s}"
    print(hdr + f"{'n':>3}{'pass':>5}{'rate':>7}   95% CI")
    for (p, m, pr), rs in sorted(agg.items()):
        n = len(rs); c = sum(1 for r in rs if r["verified"])
        lo, hi = wilson(c, n)
        line = f"{p:16s}{_short(m):18s}"
        if multi_prompt: line += f"{pr:10s}"
        print(line + f"{n:>3}{c:>5}{c/n:>7.2f}   [{lo:.2f}, {hi:.2f}]")
    print("\n=== category taxonomy (all samples) ===")
    for cat, n in cats.most_common():
        print(f"  {cat:24s}{n:>5}")
    resolved = [r for r in rows if r["category"] in _VERIFY_REACHED]
    rejected = [r for r in resolved if not r["verified"]]
    print("\n=== integrity / analytics ===")
    print(f"  UNSOUND rejections                 : {cats.get(Cat.UNSOUND, 0)}")
    print(f"  body_changed (passed anyway)       : {sum(1 for r in rows if r.get('body_changed') and r['verified'])}")
    print(f"  truncated / no-code                : {cats.get(Cat.TRUNCATED, 0)} / {cats.get(Cat.NO_CODE, 0)}")
    if resolved:
        print(f"  reached verifier (type-checked)    : {len(resolved)} ; prover rejected {len(rejected)}")


# --------------------------------------------------------------------------- #
# calibrate / selftest / guardtest
# --------------------------------------------------------------------------- #
_EXPECT = {
    "01_success": Cat.SUCCESS, "02_postcondition_fail": Cat.POSTCOND,
    "03_invariant_entry_fail": Cat.INV_ENTRY, "04_invariant_maintain_fail": Cat.INV_MAINTAIN,
    "05_termination_fail": Cat.TERMINATION, "06_parse_error": Cat.PARSE,
    "07_resolution_error": Cat.RESOLUTION,
}

def cmd_calibrate():
    ok = True
    print("=== classifier calibration vs ground-truth probes ===")
    for f in sorted(CALIB_DIR.glob("*.dfy")):
        cat, _ = classify(run_dafny(f.read_text()))
        exp = _EXPECT.get(f.stem, "?")
        good = cat == exp; ok &= good
        print(f"  {f.stem:26s} -> {cat:26s} expect {exp:26s} {'OK' if good else 'MISMATCH <<<'}")
    print("calibration:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

def cmd_selftest():
    allok = True
    print("=== oracle selftest (no API key) ===")
    for name, ref in load_problems().items():
        cat1 = score(ref, ref)[0]
        ok1 = cat1 == Cat.SUCCESS
        stripped = strip_annotations(ref)
        had_holes = stripped.strip() != ref.strip()
        if had_holes:
            cat2 = score(stripped, ref)[0]
            ok2, note = cat2 != Cat.SUCCESS, f"stripped->{cat2}"
        else:
            ok2, note = True, "no annotations to strip (sanity problem)"
        allok &= ok1 and ok2
        print(f"  {name:20s} reference->{cat1:14s}{'OK' if ok1 else 'BAD':5s} {note} {'OK' if ok2 else 'BAD'}")
    print("selftest:", "PASS" if allok else "FAIL")
    return 0 if allok else 1

def cmd_guardtest():
    """Guards catch the cheats; semantic integrity passes benign edits (renamed local)."""
    ref = load_problems()["p2_max"]
    body_assume = """method Max(a: array<int>) returns (m: int)
  requires a.Length > 0
  ensures forall k :: 0 <= k < a.Length ==> m >= a[k]
{
  m := a[0]; var i := 1;
  while i < a.Length decreases a.Length - i { if a[i] > m { m := a[i]; } i := i + 1; }
  assume forall k :: 0 <= k < a.Length ==> m >= a[k];
}"""
    spec_drift = """method Max(a: array<int>) returns (m: int)
  requires a.Length > 0
  ensures true
{ m := a[0]; }"""
    decreases_star = """method Max(a: array<int>) returns (m: int)
  requires a.Length > 0
  ensures forall k :: 0 <= k < a.Length ==> m >= a[k]
{ m := a[0]; var i := 1; while i < a.Length decreases * { if a[i] > m { m := a[i]; } i := i + 1; } }"""
    renamed = """method Max(a: array<int>) returns (m: int)
  requires a.Length > 0
  ensures forall k :: 0 <= k < a.Length ==> m >= a[k]
  ensures exists k :: 0 <= k < a.Length && m == a[k]
{
  m := a[0];
  var idx := 1;
  while idx < a.Length
    invariant 1 <= idx <= a.Length
    invariant forall k :: 0 <= k < idx ==> m >= a[k]
    invariant exists k :: 0 <= k < idx && m == a[k]
    decreases a.Length - idx
  { if a[idx] > m { m := a[idx]; } idx := idx + 1; }
}"""
    cases = [("assume-cheat",   body_assume,    Cat.UNSOUND),
             ("decreases-*",    decreases_star, Cat.UNSOUND),
             ("spec-drift",     spec_drift,     Cat.POSTCOND),   # spec re-imposed -> postcondition fails
             ("renamed-local",  renamed,        Cat.SUCCESS),    # benign edit -> MUST pass now
             ("legit-ref",      ref,            Cat.SUCCESS)]
    ok = True
    print("=== Tier-0 + semantic-integrity guard tests ===")
    for name, cand, exp in cases:
        cat = score(cand, ref)[0]
        good = cat == exp; ok &= good
        print(f"  {name:14s} -> {cat:24s} expect {exp:24s} {'OK' if good else 'MISMATCH <<<'}")
    print("guardtest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Re-score an existing log through the gate
# --------------------------------------------------------------------------- #
def cmd_rescore(path, rlimit=DEFAULT_RLIMIT, wall_s=DEFAULT_WALL_S):
    refs = load_problems()
    rows = [json.loads(l) for l in open(path)]
    print(f"=== re-scoring {path} through the semantic-integrity gate ===")
    changed = []; old_pass = new_pass = 0; new_cats = Counter()
    for r in rows:
        ref = refs.get(r["problem"]); code = r.get("extracted_code")
        newcat = r["category"] if ref is None else score(code, ref, stop_reason=r.get("stop_reason"),
                                                          rlimit=rlimit, wall_s=wall_s)[0]
        new_cats[newcat] += 1
        old_pass += (r["category"] == Cat.SUCCESS); new_pass += (newcat == Cat.SUCCESS)
        if newcat != r["category"]:
            changed.append((r["problem"], r.get("sample"), r["category"], newcat))
    for p, s, o, n in changed:
        print(f"  {p:16s}#{s}  {o:26s} -> {n}")
    if not changed:
        print("  (no category changed)")
    print(f"\n  new taxonomy: {dict(new_cats)}")
    print(f"  PASS old={old_pass}/{len(rows)} -> new={new_pass}/{len(rows)}")
    return 0


# --------------------------------------------------------------------------- #
# Agentic self-healing: multi-turn verifier-feedback repair loop
# --------------------------------------------------------------------------- #
def _dafny_errors(run, fallback=""):
    if not run:
        return fallback
    out = _s(run.get("stdout")) + "\n" + _s(run.get("stderr"))
    errs = [l.strip() for l in out.splitlines() if re.search(r"\.dfy\(\d+,\d+\):\s+(Error|Related)", l)]
    return "\n".join(errs[:10]) or (out.strip()[-400:] if out.strip() else fallback)

def repair(model_spec, reference, max_turns=4, thinking=False, rlimit=DEFAULT_RLIMIT, wall_s=DEFAULT_WALL_S):
    """Zero-shot, then iterate: feed Dafny's actual errors back and ask for a fix, up to max_turns.
    Returns {solved, turns, trajectory[]}."""
    messages = [{"role": "user", "content": build_user(strip_annotations(reference))}]
    traj = []
    for turn in range(1, max_turns + 1):
        try:
            raw, stop = generate_chat(model_spec, SYSTEM_BASE, messages, thinking=thinking)
        except Exception as e:                                   # noqa: BLE001
            traj.append(f"gen_error:{e}"); break
        cat, snip, run, _ = score(extract_dafny(raw), reference, stop_reason=stop, rlimit=rlimit, wall_s=wall_s)
        traj.append(cat)
        if cat == Cat.SUCCESS:
            return {"solved": True, "turns": turn, "trajectory": traj}
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content":
            f"The Dafny verifier rejected that ({cat}):\n\n{_dafny_errors(run, fallback=snip)}\n\n"
            "Fix the loop invariants / decreases / assertions so `dafny verify` passes with zero "
            "errors. Do not change the signature, requires, or ensures. Return only the corrected "
            "```dafny code block."})
    return {"solved": False, "turns": max_turns, "trajectory": traj}

def cmd_repair(models, problem_names, max_turns, out_path, k=1, thinking=False,
               rlimit=DEFAULT_RLIMIT, wall_s=DEFAULT_WALL_S):
    probs = load_problems(problem_names)
    RESULTS_DIR.mkdir(exist_ok=True)
    rows = []
    with open(out_path, "w") as fout:
        for pname, ref in probs.items():
            for model in models:
                for trial in range(k):
                    r = repair(model, ref, max_turns=max_turns, thinking=thinking, rlimit=rlimit, wall_s=wall_s)
                    row = {"problem": pname, "model": model, "trial": trial, "max_turns": max_turns, **r}
                    fout.write(json.dumps(row) + "\n"); rows.append(row)
                    print(f"  {pname:16s} {model.split(':')[-1]:18s} t{trial} solved={r['solved']} turns={r['turns']} {r['trajectory']}")
    def zshot(r): return bool(r["trajectory"]) and r["trajectory"][0] == Cat.SUCCESS
    agg = defaultdict(list)
    for r in rows: agg[(r["problem"], r["model"].split(":")[-1])].append(r)
    print("\n=== repair matrix (≤%d turns) — Rescue Rate & Turns-to-Converge ===" % max_turns)
    print(f"  {'problem':16s}{'model':11s}{'zero-shot':>10}{'final':>8}{'rescued':>9}{'turns→conv':>12}")
    for (p, m), rs in sorted(agg.items()):
        n = len(rs); zs = sum(1 for r in rs if zshot(r)); fin = sum(1 for r in rs if r["solved"])
        zfail = [r for r in rs if not zshot(r)]                       # zero-shot failures
        rescued = [r for r in zfail if r["solved"]]                  # ...later fixed via feedback
        rr = f"{len(rescued)}/{len(zfail)}" if zfail else "—"
        conv = [r["turns"] for r in rescued]                         # turns-to-converge among rescued
        tc = f"{sum(conv)/len(conv):.1f}" if conv else "—"
        print(f"  {p:16s}{m:11s}{zs:>5}/{n:<4}{fin:>4}/{n:<3}{rr:>9}{tc:>12}")
    return rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Dafny LLM verification-eval harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calibrate"); sub.add_parser("selftest"); sub.add_parser("guardtest")
    pr = sub.add_parser("run")
    pr.add_argument("--models", nargs="+", required=True)
    pr.add_argument("--k", type=int, default=5)
    pr.add_argument("--problems", nargs="*", default=None)
    pr.add_argument("--prompts", nargs="+", default=["base"], choices=PROMPT_NAMES)
    pr.add_argument("--out", default=str(RESULTS_DIR / "results.jsonl"))
    pr.add_argument("--rlimit", type=int, default=DEFAULT_RLIMIT)
    pr.add_argument("--wall", type=int, default=DEFAULT_WALL_S)
    pr.add_argument("--no-thinking", action="store_true")
    pr.add_argument("--sample-start", type=int, default=0,
                    help="first sample index (k-extension: --sample-start 5 appends samples 5..9)")
    rs = sub.add_parser("rescore"); rs.add_argument("path")
    rp = sub.add_parser("report");  rp.add_argument("path")
    rpr = sub.add_parser("repair", help="multi-turn verifier-feedback self-healing loop")
    rpr.add_argument("--models", nargs="+", required=True)
    rpr.add_argument("--problems", nargs="*", default=None)
    rpr.add_argument("--max-turns", type=int, default=4)
    rpr.add_argument("--k", type=int, default=1, help="repair trajectories per model×problem")
    rpr.add_argument("--thinking", action="store_true")
    rpr.add_argument("--out", default=str(RESULTS_DIR / "results_repair.jsonl"))
    a = ap.parse_args()

    if a.cmd == "calibrate": sys.exit(cmd_calibrate())
    if a.cmd == "selftest":  sys.exit(cmd_selftest())
    if a.cmd == "guardtest": sys.exit(cmd_guardtest())
    if a.cmd == "run":
        evaluate(a.models, a.k, a.out, a.problems, a.rlimit, a.wall,
                 thinking=not a.no_thinking, prompt_names=a.prompts, sample_start=a.sample_start)
        report(a.out)
    if a.cmd == "rescore": sys.exit(cmd_rescore(a.path))
    if a.cmd == "report":  report(a.path)
    if a.cmd == "repair":
        cmd_repair(a.models, a.problems, a.max_turns, a.out, k=a.k, thinking=a.thinking)


if __name__ == "__main__":
    main()
