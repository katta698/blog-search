# Feedback notifications — added 2026-08-12.
#
# Without this the votes land in DynamoDB and nothing tells anyone, which
# makes the table write-only in practice: data you have to remember to go
# and look at is data you stop looking at after the second week.
#
# Every vote sends one mail. That is only reasonable because the volume is
# inherently low -- one vote per reader per post, ever, enforced client-side
# by localStorage. If it ever becomes noisy, the fix is to filter on vote
# type here rather than to stop publishing.
resource "aws_sns_topic" "feedback" {
  name = "blog-post-feedback"
}

resource "aws_sns_topic_subscription" "feedback_email" {
  topic_arn = aws_sns_topic.feedback.arn
  protocol  = "email"
  endpoint  = var.feedback_email

  # AWS sends a confirmation link to the address and the subscription stays
  # "pending confirmation" until it is clicked. Terraform cannot do that step,
  # and will report the subscription as created either way.
  lifecycle {
    ignore_changes = [confirmation_timeout_in_minutes]
  }
}
