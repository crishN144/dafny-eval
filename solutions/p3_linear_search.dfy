// P3 — Linear search | tests: invariant over a forall-prefix ("not seen yet") | difficulty: medium
// REFERENCE: must verify.
method LinearSearch(a: array<int>, key: int) returns (idx: int)
  ensures 0 <= idx ==> idx < a.Length && a[idx] == key
  ensures idx < 0 ==> forall k :: 0 <= k < a.Length ==> a[k] != key
{
  idx := -1;
  var i := 0;
  while i < a.Length
    invariant 0 <= i <= a.Length
    invariant forall k :: 0 <= k < i ==> a[k] != key
    decreases a.Length - i
  {
    if a[i] == key { idx := i; return; }
    i := i + 1;
  }
}
