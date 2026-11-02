provider "aws" {
  region = "us-east-1"
}

module "basic" {
  source = "../../"
}
