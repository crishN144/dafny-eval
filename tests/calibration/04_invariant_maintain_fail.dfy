// EXPECT category: INVARIANT_NOT_MAINTAINED  <-- the headline failure mode
// Target stdout: "this loop invariant might not be maintained by the loop"
method SumOnes(n: int) returns (s: int)
  requires n >= 0
{
  s := 0;
  var i := 0;
  while i < n
    invariant 0 <= i <= n
    invariant s == i        // holds on entry (0==0); broken by s := s + 2
    decreases n - i
  {
    s := s + 2;             // should be s + 1 to maintain s == i
    i := i + 1;
  }
}
