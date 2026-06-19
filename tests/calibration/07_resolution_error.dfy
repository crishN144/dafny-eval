// EXPECT category: RESOLUTION_ERROR (well-formed syntax, undefined name — verifier never runs)
method Bad(x: int) returns (y: int)
  ensures y == x
{
  y := z;   // 'z' is not declared -> resolution error
}
