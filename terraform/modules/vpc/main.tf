data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  az_count = 3

  # First letter of each AZ name (e.g. "us-east-1a" → "a").
  az_letters = [for az in slice(data.aws_availability_zones.available.names, 0, local.az_count) : substr(az, length(az) - 1, 1)]

  common_tags = merge(
    {
      project = "connect"
      env     = var.name
    },
    var.tags,
  )
}

# ── VPC ──────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.common_tags, { Name = "connect-${var.name}-vpc" })
}

# ── Internet Gateway ──────────────────────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, { Name = "connect-${var.name}-igw" })
}

# ── Public Subnets (/20 each, one per AZ) ────────────────────────────────────

resource "aws_subnet" "public" {
  count = local.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.cidr, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name                     = "connect-${var.name}-public-${local.az_letters[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

# ── Private Subnets (/20 each, one per AZ) ───────────────────────────────────

resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.cidr, 4, count.index + local.az_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(local.common_tags, {
    Name                              = "connect-${var.name}-private-${local.az_letters[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

# ── NAT Gateway — single instance in AZ-a (cost-optimised) ──────────────────

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(local.common_tags, { Name = "connect-${var.name}-nat-eip" })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(local.common_tags, { Name = "connect-${var.name}-nat" })

  depends_on = [aws_internet_gateway.main]
}

# ── Route Tables ─────────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, { Name = "connect-${var.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count = local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# All three private subnets share the single NAT in AZ-a.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = merge(local.common_tags, { Name = "connect-${var.name}-private-rt" })
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
