locals {
  tags = merge({ project = "connect", env = var.name }, var.tags)
}

# ── Security group ───────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "connect-${var.name}-alb"
  description = "Internet-facing ALB for connect-${var.name}. 80 → 443 redirect; 443 → web target group."
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.ingress_cidrs
  }

  ingress {
    description = "HTTP (redirected to HTTPS by the listener)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.ingress_cidrs
  }

  egress {
    description = "All egress (ALB needs to reach targets in private subnets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.name}-alb" })
}

# ── ALB ──────────────────────────────────────────────────────────────────────

resource "aws_lb" "this" {
  name               = "connect-${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
  idle_timeout       = var.idle_timeout

  drop_invalid_header_fields = true

  tags = merge(local.tags, { Name = "connect-${var.name}-alb" })
}

# ── Target group: web service (Fargate IP-target) ────────────────────────────

resource "aws_lb_target_group" "web" {
  name        = "connect-${var.name}-web"
  port        = var.web_target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.web_health_check_path
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200-299"
  }

  tags = merge(local.tags, { Name = "connect-${var.name}-web" })
}

# ── Listeners ────────────────────────────────────────────────────────────────

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_cert_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}
