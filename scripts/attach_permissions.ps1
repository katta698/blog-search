$UserName = "terraform-user"

$Policy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3Full",
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": "*"
    },
    {
      "Sid": "LambdaFull",
      "Effect": "Allow",
      "Action": ["lambda:*"],
      "Resource": "*"
    },
    {
      "Sid": "APIGatewayFull",
      "Effect": "Allow",
      "Action": ["apigateway:*", "execute-api:*"],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchFull",
      "Effect": "Allow",
      "Action": ["logs:*", "cloudwatch:*"],
      "Resource": "*"
    },
    {
      "Sid": "IAMFull",
      "Effect": "Allow",
      "Action": ["iam:*"],
      "Resource": "*"
    },
    {
      "Sid": "EventBridgeFull",
      "Effect": "Allow",
      "Action": ["events:*", "scheduler:*"],
      "Resource": "*"
    },
    {
      "Sid": "BedrockFull",
      "Effect": "Allow",
      "Action": ["bedrock:*"],
      "Resource": "*"
    }
  ]
}
'@

aws iam put-user-policy `
  --user-name $UserName `
  --policy-name "BlogSearchTerraformPolicy" `
  --policy-document $Policy

Write-Host "Done — inline policy attached to $UserName" -ForegroundColor Green
