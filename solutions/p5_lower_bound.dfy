// P5 — LowerBound (mutated binary search) | tests: boundary invariant + off-by-one | MUTATED (Tier 1)
// First index where a[k] >= key. Different postcondition from textbook BinarySearch.
// REFERENCE: must verify. Annotations (invariant/decreases) are the infill target.
method LowerBound(a: array<int>, key: int) returns (idx: int)
  requires forall p, q :: 0 <= p < q < a.Length ==> a[p] <= a[q]
  ensures 0 <= idx <= a.Length
  ensures forall k :: 0 <= k < idx ==> a[k] < key
  ensures forall k :: idx <= k < a.Length ==> a[k] >= key
{
  var lo, hi := 0, a.Length;
  while lo < hi
    invariant 0 <= lo <= hi <= a.Length
    invariant forall k :: 0 <= k < lo ==> a[k] < key
    invariant forall k :: hi <= k < a.Length ==> a[k] >= key
    decreases hi - lo
  {
    var mid := lo + (hi - lo) / 2;
    if a[mid] < key {
      lo := mid + 1;
    } else {
      hi := mid;
    }
  }
  idx := lo;
}
