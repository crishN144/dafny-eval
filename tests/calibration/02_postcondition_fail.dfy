// EXPECT category: POSTCONDITION_FAIL
// Target stdout: "a postcondition could not be proved on this return path"
method Abs(x: int) returns (y: int)
  ensures y >= 0
  ensures y == x || y == -x
{
  y := x; // wrong when x < 0: violates y >= 0
}
