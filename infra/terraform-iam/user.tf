# No console login and no access key here. An access key created by Terraform
# keeps its secret in plain text in the state file, so the key is made once in
# the console, where the secret is shown a single time and stored nowhere.
resource "aws_iam_user" "deployer" {
  name = "arxiv-lens-deployer"
}

resource "aws_iam_user_policy_attachment" "deployer" {
  user       = aws_iam_user.deployer.name
  policy_arn = aws_iam_policy.deployer.arn
}

resource "aws_iam_user_policy_attachment" "deployer_iam" {
  user       = aws_iam_user.deployer.name
  policy_arn = aws_iam_policy.deployer_iam.arn
}
