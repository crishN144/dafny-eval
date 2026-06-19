// EXPECT category: PARSE_ERROR (syntactic — verifier never runs)
method Bad(x: int) returns (y: int) {
  y := ;   // missing right-hand expression -> parse error
}
