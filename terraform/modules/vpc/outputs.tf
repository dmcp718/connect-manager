output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the three public /20 subnets (one per AZ)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the three private /20 subnets (one per AZ)."
  value       = aws_subnet.private[*].id
}

output "nat_eip" {
  description = "Public IP of the single NAT Gateway EIP (AZ-a)."
  value       = aws_eip.nat.public_ip
}
