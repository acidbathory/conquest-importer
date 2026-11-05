# Verification stack: real RDS, ElastiCache, ECS, EBS, NLB, classic ELB and S3 request metrics, so
# the importer's New Relic metric names can be checked against data. Everything is prefixed ckv-,
# tagged, and removed with `terraform destroy` (state is local and git-ignored).
#
# COST: roughly $0.15 per hour while it exists (RDS, ElastiCache, two load balancers, a small
# instance and a Fargate task). Destroy it as soon as the check is done.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.67" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "my_ip_cidr" {
  description = "Your public IP as a /32. Only this address can reach the load balancers."
  type        = string
}

provider "aws" {
  region = var.region

  default_tags {
    tags = { owner = "conquest-importer-verify" }
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ---- network access ------------------------------------------------------------------------

resource "aws_security_group" "private" {
  name        = "ckv-private"
  description = "ckv: no inbound from outside; used by RDS, ElastiCache and the instance"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "members of this group"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "web" {
  name        = "ckv-web"
  description = "ckv: port 80 from one address, for generating load balancer traffic"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "http from the person running the check"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  egress {
    description = "all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---- EC2 and EBS ---------------------------------------------------------------------------

resource "aws_instance" "host" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.micro"
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.private.id]

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = { Name = "ckv-host" }
}

resource "aws_ebs_volume" "extra" {
  availability_zone = aws_instance.host.availability_zone
  size              = 1
  type              = "gp3"

  tags = { Name = "ckv-extra" }
}

resource "aws_volume_attachment" "extra" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.extra.id
  instance_id = aws_instance.host.id
}

# ---- RDS -------------------------------------------------------------------------------------

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "db" {
  name       = "ckv-db"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "db" {
  identifier              = "ckv-db"
  engine                  = "postgres"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  username                = "ckvadmin"
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.db.name
  vpc_security_group_ids  = [aws_security_group.private.id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  backup_retention_period = 0
  deletion_protection     = false
  apply_immediately       = true
}

# ---- ElastiCache -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "cache" {
  name       = "ckv-cache"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_elasticache_cluster" "cache" {
  cluster_id           = "ckv-cache"
  engine               = "redis"
  node_type            = "cache.t4g.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.cache.name
  security_group_ids   = [aws_security_group.private.id]
}

# ---- ECS on Fargate ----------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = "ckv-cluster"
}

resource "aws_ecs_task_definition" "web" {
  family                   = "ckv-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512

  container_definitions = jsonencode([
    {
      name         = "web"
      image        = "public.ecr.aws/nginx/nginx:stable-alpine"
      essential    = true
      portMappings = [{ containerPort = 80, protocol = "tcp" }]
    }
  ])
}

resource "aws_ecs_service" "web" {
  name            = "ckv-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.private.id]
    assign_public_ip = true
  }
}

# ---- load balancers ----------------------------------------------------------------------------

resource "aws_elb" "classic" {
  name            = "ckv-classic"
  subnets         = slice(data.aws_subnets.default.ids, 0, 2)
  security_groups = [aws_security_group.web.id]
  instances       = [aws_instance.host.id]

  listener {
    instance_port     = 80
    instance_protocol = "http"
    lb_port           = 80
    lb_protocol       = "http"
  }

  health_check {
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 3
    target              = "HTTP:80/"
    interval            = 30
  }
}

resource "aws_lb" "network" {
  name               = "ckv-network"
  load_balancer_type = "network"
  subnets            = slice(data.aws_subnets.default.ids, 0, 2)
}

resource "aws_lb_target_group" "network" {
  name        = "ckv-network"
  port        = 80
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_lb_target_group_attachment" "network" {
  target_group_arn = aws_lb_target_group.network.arn
  target_id        = aws_instance.host.id
}

resource "aws_lb_listener" "network" {
  load_balancer_arn = aws_lb.network.arn
  port              = 80
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.network.arn
  }
}

# ---- S3 with request metrics -----------------------------------------------------------------

resource "aws_s3_bucket" "data" {
  bucket        = "ckv-data-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_metric" "entire" {
  bucket = aws_s3_bucket.data.id
  name   = "EntireBucket"
}

output "dimensions" {
  description = "Values the check script uses for CloudWatch dimensions and traffic."
  value = {
    db_instance   = aws_db_instance.db.identifier
    cache_cluster = aws_elasticache_cluster.cache.cluster_id
    ecs_cluster   = aws_ecs_cluster.main.name
    ecs_service   = aws_ecs_service.web.name
    volume_root   = aws_instance.host.root_block_device[0].volume_id
    volume_extra  = aws_ebs_volume.extra.id
    instance      = aws_instance.host.id
    classic_name  = aws_elb.classic.name
    classic_dns   = aws_elb.classic.dns_name
    nlb_dimension = aws_lb.network.arn_suffix
    nlb_dns       = aws_lb.network.dns_name
    bucket        = aws_s3_bucket.data.bucket
  }
}
