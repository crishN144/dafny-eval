// P2 — Max of array | tests: one loop invariant + array-bounds + witness | difficulty: easy
// REFERENCE: must verify. The stripped `invariant`/`decreases` lines are what the model must restore.
method Max(a: array<int>) returns (m: int)
  requires a.Length > 0
  ensures forall k :: 0 <= k < a.Length ==> m >= a[k]
  ensures exists k :: 0 <= k < a.Length && m == a[k]
{
  m := a[0];
  var i := 1;
  while i < a.Length
    invariant 1 <= i <= a.Length
    invariant forall k :: 0 <= k < i ==> m >= a[k]
    invariant exists k :: 0 <= k < i && m == a[k]
    decreases a.Length - i
  {
    if a[i] > m { m := a[i]; }
    i := i + 1;
  }
}
