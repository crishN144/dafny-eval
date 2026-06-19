// P6 — Max profit: max a[j]-a[i] for i<j (single-pass running-best) | NOVEL (Tier 1)
// Same single-pass "running auxiliary (min-so-far) + running best" shape as Kadane, with a
// clean sum-free spec. Ghost witnesses (bi/bj for the best pair) are FIXED scaffolding;
// the loop invariants are the infill target.
// REFERENCE: must verify.
method MaxProfit(a: array<int>) returns (best: int)
  requires a.Length >= 2
  ensures exists i, j :: 0 <= i < j < a.Length && best == a[j] - a[i]
  ensures forall i, j :: 0 <= i < j < a.Length ==> best >= a[j] - a[i]
{
  best := a[1] - a[0];
  var minIdx := if a[0] <= a[1] then 0 else 1;
  ghost var bi, bj := 0, 1;
  var j := 2;
  while j < a.Length
    invariant 2 <= j <= a.Length
    invariant 0 <= minIdx < j
    invariant forall k :: 0 <= k < j ==> a[minIdx] <= a[k]
    invariant 0 <= bi < bj < j && best == a[bj] - a[bi]
    invariant forall p, q :: 0 <= p < q < j ==> best >= a[q] - a[p]
    decreases a.Length - j
  {
    if a[j] - a[minIdx] > best {
      best := a[j] - a[minIdx];
      bi := minIdx;
      bj := j;
    }
    if a[j] < a[minIdx] {
      minIdx := j;
    }
    j := j + 1;
  }
}
