// EXPECT category: INVARIANT_ENTRY_FAIL
// Target stdout: "this loop invariant could not be proved on entry" (or "might not hold on entry")
method CountUp(n: int) returns (c: int)
  requires n >= 0
{
  c := 0;
  var i := 0;
  while i < n
    invariant 1 <= i <= n   // FALSE on entry: i == 0
    decreases n - i
  {
    c := c + 1;
    i := i + 1;
  }
}
