// EXPECT category: FULL_SUCCESS
// Target stdout: "Dafny program verifier finished with 1 verified, 0 errors"
method Abs(x: int) returns (y: int)
  ensures y >= 0
  ensures y == x || y == -x
{
  if x < 0 { y := -x; } else { y := x; }
}
