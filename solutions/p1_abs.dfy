// P1 — Abs | tests: postcondition only, no loop | difficulty: trivial (sanity)
// REFERENCE: must verify. Annotations stripped by the harness = the model-facing task.
method Abs(x: int) returns (y: int)
  ensures y >= 0
  ensures y == x || y == -x
{
  if x < 0 { y := -x; } else { y := x; }
}
