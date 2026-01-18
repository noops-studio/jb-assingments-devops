# AWS Infrastructure Automation

Automated deployment of a web application on AWS with Application Load Balancer, Auto Scaling Group, and EC2 instances.

## Features

- **Application Load Balancer (ALB)** with health checks on `/health`
- **Auto Scaling Group (ASG)** with CPU-based scaling policies
- **EC2 instances** running a Flask application showing system metrics
- **Security Groups** configured for proper network isolation
- **Python automation** with full deploy/destroy capabilities
- **State management** via `state.json` for reliable teardown

## Prerequisites

1. AWS Account with appropriate permissions
2. AWS credentials configured (via `~/.aws/credentials` or environment variables)
3. Python 3.7+
4. Required Python packages (install via `pip install -r requirements.txt`)

## AWS Permissions Required

The IAM user/role needs permissions for:
- EC2 (VPC, Subnets, Security Groups, Instances, Launch Templates)
- ELBv2 (Load Balancers, Target Groups, Listeners)
- Auto Scaling (Groups, Policies)
- IAM (Roles, Instance Profiles)
- CloudWatch (for scaling metrics)

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Edit `config.yaml` to customize:

- `region`: AWS region (default: us-east-1)
- `instance_type`: EC2 instance type (default: t3.micro)
- `min_capacity`: Minimum ASG size (default: 1)
- `desired_capacity`: Desired ASG size (default: 2)
- `max_capacity`: Maximum ASG size (default: 3)
- `scaling_policy`: CPU thresholds for scaling
- `vpc`: VPC and subnet configuration

## Usage

### Deploy Infrastructure

```bash
python deploy.py deploy --env dev
```

This will create:
- VPC with subnets
- Security Groups (ALB and EC2)
- Application Load Balancer
- Target Group with health checks
- Launch Template
- Auto Scaling Group
- IAM role for EC2 instances

### Check Status

```bash
python deploy.py status --env dev
```

Shows:
- ALB DNS name
- Target Group health status
- ASG size and instance count

### Destroy Infrastructure

```bash
python deploy.py destroy --env dev
```

Removes all resources in the correct order:
1. Auto Scaling Group (terminates instances)
2. Launch Template
3. ALB and Listeners
4. Target Group
5. Security Groups
6. Subnets
7. Internet Gateway
8. VPC
9. IAM Role and Instance Profile

## Application Endpoints

Once deployed, access the application via the ALB DNS name:

- `http://<alb-dns>/` - Returns hostname, CPU usage, memory usage, and disk free space
- `http://<alb-dns>/health` - Health check endpoint (returns 200 OK)

Example output from `/`:
```
Hello from ip-10-0-1-23
CPU usage: 23%
Memory usage: 41%
Disk free: 18 GB
```

## Architecture

- **VPC**: Custom VPC with public subnets across multiple availability zones
- **ALB**: Internet-facing Application Load Balancer on port 80
- **Target Group**: HTTP target group with health checks on `/health`
- **ASG**: Auto Scaling Group with min=1, desired=2, max=3 instances
- **Scaling Policy**: CPU-based target tracking (scale-out at 50%, scale-in at 20%)
- **Security**: EC2 instances only accessible from ALB, ALB accessible from internet

## State Management

The deployment tool uses `state.json` to track created resources. This ensures:
- Reliable resource tracking across operations
- Clean teardown of all resources
- Support for multiple environments

## Testing

1. Deploy: `python deploy.py deploy --env dev`
2. Wait for instances to become healthy (check status)
3. Access ALB DNS and verify `/` and `/health` endpoints
4. Refresh `/` multiple times to see different hostnames (load balancing)
5. Destroy: `python deploy.py destroy --env dev`

## Troubleshooting

- **Instances not becoming healthy**: Check security groups, ensure UserData script completed successfully
- **ALB not responding**: Verify ALB is in "active" state, check security group rules
- **Destroy fails**: Some resources may need manual cleanup if dependencies exist

## Notes

- The UserData script installs Python 3, Flask, and psutil on Amazon Linux 2
- The application runs as a systemd service for automatic restart
- Port 80 requires root privileges on EC2 instances
- First deployment may take 5-10 minutes for all resources to be ready
