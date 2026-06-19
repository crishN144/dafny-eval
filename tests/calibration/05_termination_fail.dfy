// EXPECT category: TERMINATION_FAIL
// Target stdout: "cannot prove termination" / "decreases expression might not decrease"
method MightNotTerminate(n: int)
{
  var i := 0;
  while i < n           // no decreases clause; Dafny's guess (n - i) increases as i falls
  {
    i := i - 1;
  }
}
