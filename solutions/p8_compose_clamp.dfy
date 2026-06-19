// P8 — Compositional: helper function + method that must thread its contract | COMPOSITIONAL (Tier 1)
// Two top-level declarations. The model must restore loop invariants in ClampArray that reference
// the helper function ClampLow and rely on its postcondition — cross-declaration contract alignment.
// (Each piece is individually trivial to verify; the alignment is the test.)
// REFERENCE: must verify. The four loop invariants are the infill target.
function ClampLow(x: int, lo: int): int
  ensures ClampLow(x, lo) >= lo
  ensures ClampLow(x, lo) == x || ClampLow(x, lo) == lo
{
  if x < lo then lo else x
}

method ClampArray(a: array<int>, lo: int) returns (b: array<int>)
  ensures b.Length == a.Length
  ensures forall k :: 0 <= k < a.Length ==> b[k] == ClampLow(a[k], lo)
  ensures forall k :: 0 <= k < a.Length ==> b[k] >= lo
{
  b := new int[a.Length];
  var i := 0;
  while i < a.Length
    invariant 0 <= i <= a.Length
    invariant b.Length == a.Length
    invariant forall k :: 0 <= k < i ==> b[k] == ClampLow(a[k], lo)
    invariant forall k :: 0 <= k < i ==> b[k] >= lo
    decreases a.Length - i
  {
    b[i] := ClampLow(a[i], lo);
    i := i + 1;
  }
}
