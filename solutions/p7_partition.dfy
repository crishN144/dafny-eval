// P7 — In-place partition around a pivot (two-pointer, Lomuto) | tests: array mutation +
// framing + multiset-preservation invariant via swaps | NOVEL (Tier 1)
// (Chosen over set-preserving dedup: same mutation/framing/two-pointer skills and a genuine
//  two-region invariant, but multiset-under-swap verifies cheaply where set-image
//  comprehensions force proof-plumbing.)
// REFERENCE: must verify. The four loop invariants are the infill target.
method Partition(a: array<int>, pivot: int) returns (p: int)
  modifies a
  ensures 0 <= p <= a.Length
  ensures forall k :: 0 <= k < p ==> a[k] < pivot
  ensures forall k :: p <= k < a.Length ==> a[k] >= pivot
  ensures multiset(a[..]) == multiset(old(a[..]))
{
  p := 0;
  var i := 0;
  while i < a.Length
    invariant 0 <= p <= i <= a.Length
    invariant forall k :: 0 <= k < p ==> a[k] < pivot
    invariant forall k :: p <= k < i ==> a[k] >= pivot
    invariant multiset(a[..]) == multiset(old(a[..]))
    decreases a.Length - i
  {
    if a[i] < pivot {
      a[p], a[i] := a[i], a[p];
      p := p + 1;
    }
    i := i + 1;
  }
}
