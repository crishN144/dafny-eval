// P4 — Binary search | tests: invariant + bounds + decreases termination + off-by-one | THE DISCRIMINATOR
// REFERENCE: must verify. Requires a sorted array; the sortedness precondition is what lets the
// prover discharge "key is not in the discarded half" — the step models most often get wrong.
method BinarySearch(a: array<int>, key: int) returns (idx: int)
  requires forall i, j :: 0 <= i < j < a.Length ==> a[i] <= a[j]
  ensures 0 <= idx ==> idx < a.Length && a[idx] == key
  ensures idx < 0 ==> forall k :: 0 <= k < a.Length ==> a[k] != key
{
  var lo, hi := 0, a.Length;
  while lo < hi
    invariant 0 <= lo <= hi <= a.Length
    invariant forall k :: 0 <= k < lo ==> a[k] != key
    invariant forall k :: hi <= k < a.Length ==> a[k] != key
    decreases hi - lo
  {
    var mid := lo + (hi - lo) / 2;
    if a[mid] < key {
      lo := mid + 1;
    } else if a[mid] > key {
      hi := mid;
    } else {
      idx := mid;
      return;
    }
  }
  idx := -1;
}
