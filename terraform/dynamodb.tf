# Post feedback ("Was this useful?") — added 2026-08-12.
#
# One item per voter per post, not one per vote. The sort key is what decides
# that, and it decides more than it looks like it does:
#
#   voted_at (the original)  every vote is a new row, so a reader who changes
#                            their mind is counted twice and one who clicks a
#                            reason chip after the thumb is counted twice again.
#   voter    (this)          a second vote from the same browser overwrites the
#                            first, so changing your mind is a correction rather
#                            than a second opinion.
#
# The timestamp survives as a plain attribute, so "which posts are people
# marking as unclear, and when" is still answerable -- that question needs the
# timing, not a timestamp in the key.
#
# PAY_PER_REQUEST, and this is the one case where it is not a judgement call:
# the table takes a handful of writes a day with no floor to provision against,
# and provisioned capacity only wins above roughly 29% sustained utilisation.
# Nothing here comes close.
resource "aws_dynamodb_table" "feedback" {
  name         = "blog-post-feedback"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slug"
  range_key    = "voter"

  attribute {
    name = "slug"
    type = "S"
  }

  # An opaque random id the browser generates once and keeps in localStorage.
  # It identifies a browser, not a person: no address, no fingerprinting, it
  # does not survive clearing site data, and it is not correlated across posts
  # by anything other than itself.
  attribute {
    name = "voter"
    type = "S"
  }

  # Votes are not worth losing, and at this volume PITR costs cents.
  point_in_time_recovery {
    enabled = true
  }

  # The key schema is settled now. Any future change that forces replacement
  # has to take this off deliberately, which is the point: by then the table
  # holds votes, and replacing it would delete them with no warning.
  lifecycle {
    prevent_destroy = true
  }
}
