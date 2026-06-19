// P10 — Compositional predicate-preservation (3 declarations) | COMPOSITIONAL (Tier 1)
// A method maintains a `predicate`-defined invariant across a loop, using a helper function
// specified (via `ensures`) to establish it. The model must restore a loop invariant that
// REFERENCES the predicate, and its maintenance relies on the helper's contract + framing.
// REFERENCE: must verify.
predicate AllPos(a: array<int>, n: int)
  requires 0 <= n <= a.Length
  reads a
{ forall k :: 0 <= k < n ==> a[k] > 0 }

function Bump(x: int): int
  ensures Bump(x) > 0
{ if x > 0 then x else 1 }

method MakePositive(a: array<int>) returns (b: array<int>)
  ensures b.Length == a.Length
  ensures AllPos(b, b.Length)
{
  b := new int[a.Length];
  var i := 0;
  while i < a.Length
    invariant 0 <= i <= a.Length
    invariant b.Length == a.Length
    invariant AllPos(b, i)
    decreases a.Length - i
  {
    b[i] := Bump(a[i]);
    i := i + 1;
  }
}
