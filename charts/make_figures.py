#!/usr/bin/env python3
"""
Publication-quality figures for dafny-eval.
Reads results/*.jsonl, writes figures/*.png at 300 dpi.

Run:  .venv/bin/python charts/make_figures.py      (needs: matplotlib)

Three figures:
  fig1 — capability matrix (n=30, Wilson 95% CIs): the saturation + lone P6 discriminator
  fig2 — the "Prompt Paradox": P6 pass rate is non-monotonic in prompt scaffolding
  fig3 — agentic repair: verifier feedback rescues fluency failures, not reasoning failures
"""
import json, math
from pathlib import Path
import matplotlib as mpl
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
FIGS = ROOT / "figures"; FIGS.mkdir(exist_ok=True)

# ----------------------------------------------------------------------------- #
# House style — minimalist, hierarchical type, decluttered, colourblind-safe
# ----------------------------------------------------------------------------- #
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 11,
    "axes.labelsize": 11.5, "axes.labelcolor": "#222",
    "xtick.labelsize": 10, "ytick.labelsize": 10, "xtick.color": "#333", "ytick.color": "#333",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#666", "axes.linewidth": 0.9, "axes.axisbelow": True,
    "grid.color": "#D9D9D9", "grid.linestyle": (0, (3, 3)), "grid.linewidth": 0.6,
    "figure.facecolor": "white", "savefig.dpi": 300, "savefig.bbox": "tight",
    "legend.frameon": False, "legend.fontsize": 9.5,
})

# capability gradient (sequential blue — reads as Haiku→Opus); Okabe–Ito accents
C       = {"haiku": "#A9C4DE", "sonnet": "#4C78A8", "opus": "#1B3A5B"}
ACCENT  = "#E69F00"   # discriminator highlight (orange)
RESCUE  = "#009E73"   # bluish green
PERSIST = "#D55E00"   # vermillion
NOTE    = dict(boxstyle="round,pad=0.4", fc="#FFF7E8", ec=ACCENT, lw=1.0)
MODELS  = [("claude-haiku-4-5", "Haiku 4.5"), ("claude-sonnet-4-6", "Sonnet 4.6"),
           ("claude-opus-4-8", "Opus 4.8")]

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, c - h), min(1.0, c + h))

def load(name):
    f = ROOT / "results" / name
    return [json.loads(l) for l in open(f)] if f.exists() else []

def fam(m): return "haiku" if "haiku" in m else "sonnet" if "sonnet" in m else "opus"
def title(ax, t, sub):
    ax.text(0, 1.12, t, transform=ax.transAxes, fontsize=14, fontweight="bold", color="#1a1a1a")
    ax.text(0, 1.045, sub, transform=ax.transAxes, fontsize=9.5, color="#777")
def y_only(ax): ax.grid(axis="x", visible=False); ax.grid(axis="y", visible=True)

MASTER = load("results_master.jsonl")
REPAIR = load("results_repair.jsonl")


# ----------------------------------------------------------------------------- #
def fig1_capability_matrix():
    probs  = ["p1_abs","p2_max","p3_linear_search","p4_binary_search","p5_lower_bound","p6_max_profit","p7_partition"]
    labels = ["Abs","Max","LinSrch","BinSrch","LowerBd","MaxProfit","Partition"]
    agg = {(p, fam(m)): [] for p in probs for m, _ in MODELS}
    for r in MASTER:
        key = (r["problem"], fam(r["model"]))
        if key in agg: agg[key].append(r["verified"])
    fig, ax = plt.subplots(figsize=(9.2, 4.7))
    w, xs = 0.26, list(range(len(probs)))
    for j, (mkey, mlab) in enumerate(MODELS):
        f = fam(mkey); rate, lo, hi = [], [], []
        for p in probs:
            v = agg[(p, f)]; c = sum(v); n = len(v); rt = c / n if n else 0
            a, b = wilson(c, n); rate.append(rt); lo.append(rt - a); hi.append(b - rt)
        ax.bar([x + (j - 1) * w for x in xs], rate, w, yerr=[lo, hi], capsize=2.5, color=C[f],
               label=mlab, zorder=3, error_kw=dict(ecolor="#2b2b2b", elinewidth=0.9, capthick=0.9))
    ax.axhline(1.0, ls=(0, (5, 4)), lw=0.9, color="#9a9a9a", zorder=1)
    ax.text(len(probs) - 0.55, 1.015, "saturation ceiling (1.00)", ha="right", va="bottom",
            fontsize=8.5, color="#777", style="italic")
    p6 = probs.index("p6_max_profit")
    ax.axvspan(p6 - 0.5, p6 + 0.5, color=ACCENT, alpha=0.09, zorder=0)
    ax.annotate("P6 — the only discriminator.\nHaiku < Sonnet < Opus,\nnon-overlapping 95% CIs",
                xy=(p6 - 0.27, 0.47), xytext=(p6 - 3.1, 0.22), fontsize=9, color="#7a4f00", va="center",
                arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=1.4), bbox=NOTE)
    ax.set_xticks(xs); ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.08); ax.set_yticks([0, .25, .5, .75, 1.0]); ax.set_ylabel("verified pass rate  (n = 30)")
    title(ax, "Frontier models saturate verified-Dafny invariant synthesis",
          "3 models × 7 problems × k=10 = 630 runs · 95% Wilson confidence intervals")
    y_only(ax)
    ax.legend(loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.21), columnspacing=1.6, handlelength=1.2)
    fig.savefig(FIGS / "fig1_capability_matrix.png"); plt.close(fig)


def fig2_prompt_paradox():
    prompts = ["base", "versioned", "fewshot"]
    plabels = ["base\n(zero-shot)", "versioned\n(+ syntax reminder)", "fewshot\n(+ worked example)"]
    agg = {(fam(m), pr): [] for m, _ in MODELS for pr in prompts}
    for r in MASTER:
        if r["problem"] == "p6_max_profit":
            agg[(fam(r["model"]), r["prompt"])].append(r["verified"])
    fig, ax = plt.subplots(figsize=(7.6, 4.7))
    xs = list(range(len(prompts)))
    ax.axvspan(0.72, 1.28, color=ACCENT, alpha=0.09, zorder=0)
    for mkey, mlab in MODELS:
        f = fam(mkey); rate, lo, hi = [], [], []
        for pr in prompts:
            v = agg[(f, pr)]; c = sum(v); n = len(v); rt = c / n if n else 0
            a, b = wilson(c, n); rate.append(rt); lo.append(rt - a); hi.append(b - rt)
        ax.errorbar(xs, rate, yerr=[lo, hi], marker="o", ms=7.5, lw=2.2, color=C[f], capsize=3,
                    elinewidth=0.9, label=mlab, zorder=3, mec="white", mew=0.8)
    ax.annotate("The Prompt Paradox —\nthe explicit syntax reminder\nLOWERS the pass rate",
                xy=(1.0, 0.30), xytext=(1.30, 0.08), fontsize=9, color="#7a4f00", va="center",
                arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=1.4), bbox=NOTE)
    ax.set_xticks(xs); ax.set_xticklabels(plabels); ax.set_xlim(-0.35, 2.35)
    ax.set_ylim(0, 1.08); ax.set_yticks([0, .25, .5, .75, 1.0]); ax.set_ylabel("P6 pass rate  (n = 10 per prompt)")
    title(ax, "Prompt scaffolding is non-monotonic at the sub-frontier",
          "P6 (MaxProfit) · base / versioned / fewshot · 95% Wilson CIs")
    y_only(ax)
    ax.legend(loc="lower left")
    fig.savefig(FIGS / "fig2_prompt_paradox.png"); plt.close(fig)


def fig3_repair_dichotomy():
    rows  = [r for r in REPAIR if r["problem"] == "p6_max_profit" and "haiku" in r["model"]]
    zfail = [r for r in rows if not (r["trajectory"] and r["trajectory"][0] == "FULL_SUCCESS")]
    reason = lambda r: any("INVARIANT" in c for c in r["trajectory"])
    groups = [("Fluency only\n(RESOLUTION / PARSE)", [r for r in zfail if not reason(r)]),
              ("Hit a reasoning error\n(INVARIANT_*)",   [r for r in zfail if reason(r)])]
    fig, ax = plt.subplots(figsize=(6.8, 4.7))
    for i, (lab, grp) in enumerate(groups):
        n = len(grp); resc = sum(1 for r in grp if r["solved"]); pers = n - resc
        ax.bar(i, resc, 0.52, color=RESCUE, zorder=3, label="rescued by feedback" if i == 0 else None)
        ax.bar(i, pers, 0.52, bottom=resc, color=PERSIST, zorder=3, label="persisted (unrescued)" if i == 0 else None)
        ax.text(i, n + 0.09, f"{resc}/{n} rescued", ha="center", fontsize=10.5, fontweight="bold", color="#222")
    ax.annotate("fixed in ~1\nfeedback turn", xy=(0.0, 1.5), xytext=(0.40, 2.15), fontsize=8.8, color="#00674c",
                arrowprops=dict(arrowstyle="-|>", color=RESCUE, lw=1.2))
    ax.annotate("thrashed across\nall 4 turns", xy=(1.0, 1.0), xytext=(1.18, 1.75), fontsize=8.8, color="#8a3500",
                arrowprops=dict(arrowstyle="-|>", color=PERSIST, lw=1.2))
    ax.set_xticks([0, 1]); ax.set_xticklabels([g[0] for g in groups]); ax.set_xlim(-0.7, 1.95)
    ax.set_ylim(0, 3.5); ax.set_yticks([0, 1, 2, 3]); ax.set_ylabel("zero-shot failures (count)")
    title(ax, "Verifier feedback fixes syntax, not reasoning",
          "Haiku × P6 agentic repair · ≤4 turns · n=%d zero-shot failures (illustrative)" % len(zfail))
    y_only(ax)
    ax.legend(loc="upper right")
    fig.savefig(FIGS / "fig3_repair_dichotomy.png"); plt.close(fig)


if __name__ == "__main__":
    fig1_capability_matrix()
    fig2_prompt_paradox()
    fig3_repair_dichotomy()
    print("wrote:", ", ".join(p.name for p in sorted(FIGS.glob("*.png"))))
