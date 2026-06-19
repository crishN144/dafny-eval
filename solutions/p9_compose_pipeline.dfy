// P9 — Compositional pipeline (4 declarations) | COMPOSITIONAL (Tier 1)
// Three chained helper functions whose contracts must align across NESTED calls
// (each function's `ensures` discharges the next function's `requires`), plus a method whose
// loop invariant must compose all three semantics. The infill target is the loop invariant +
// bounds; the cross-function contract threading is checked by Dafny at the call site.
// REFERENCE: must verify.
function StepA(x: int): int
  requires x >= 0
  ensures StepA(x) == x + 1
{ x + 1 }

function StepB(x: int): int
  requires x >= 1
  ensures StepB(x) == 2 * x
{ 2 * x }

function StepC(x: int): int
  requires x >= 2
  ensures StepC(x) == x - 1
{ x - 1 }

method Pipeline(a: array<int>) returns (b: array<int>)
  requires forall k :: 0 <= k < a.Length ==> a[k] >= 0
  ensures b.Length == a.Length
  ensures forall k :: 0 <= k < a.Length ==> b[k] == 2 * a[k] + 1
{
  b := new int[a.Length];
  var i := 0;
  while i < a.Length
    invariant 0 <= i <= a.Length
    invariant b.Length == a.Length
    invariant forall k :: 0 <= k < i ==> b[k] == 2 * a[k] + 1
    decreases a.Length - i
  {
    b[i] := StepC(StepB(StepA(a[i])));
    i := i + 1;
  }
}
