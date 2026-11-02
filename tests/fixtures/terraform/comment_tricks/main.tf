# grant read on bucket/*
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

/* legacy */
resource "aws_s3_object" "o" {
  source = "./"
}

locals {
  doc = <<-EOT
    provider "fake" {
  EOT
}
