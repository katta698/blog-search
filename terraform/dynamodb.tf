# Post feedback ("Was this useful?") — added 2026-08-12.
#
# One item per vote rather than a counter, because the interesting question is
# not "what is the score" but "which posts are people marking as unclear or
# wrong, and when". A counter throws away the timing, and the timing is what
# tells you whether a rewrite helped.
#
# PAY_PER_REQUEST, and this is the one case where it is not a judgement call:
# the table takes a handful of writes a day with no floor to provision against,
# and provisioned capacity only wins above roughly 29% sustained utilisation.
# Nothing here comes close.
resource "aws_dynamodb_table" "feedback" {
  name         = "blog-post-feedback"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slug"
  range_key    = "voted_at"

  attribute {
    name = "slug"
    type = "S"
  }

  attribute {
    name = "voted_at"
    type = "S"
  }

  # Votes are not worth losing, and at this volume PITR costs cents.
  point_in_time_recovery {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
