# AWS Infrastructure Automation

Fully automated AWS infrastructure deployment system for a simple web application with S3, ALB, ASG, and EC2 instances.

## Features

- Automated infrastructure provisioning using Python and boto3
- Real-time server metrics dashboard with WebSocket updates
- Application Load Balancer (ALB) with health checks
- Auto Scaling Group (ASG) with CPU-based scaling policies
- VPC with public subnets and security groups
- CloudWatch alarms for auto-scaling
- SQLite-based state management
- Complete deploy/status/destroy CLI
- Beautiful GUI showing CPU, memory, and disk usage per server

## Prerequisites

- Python 3.7+
- AWS CLI configured with appropriate credentials
- AWS permissions for EC2, ELB, Auto Scaling, CloudWatch, VPC
- Existing EC2 Launch Template: `lt-0615440f322f6e574`

## Installation

1. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure AWS credentials:

Option 1: Using .env file (recommended)
```bash
cp env.example .env
# Edit .env and add your AWS credentials:
# AWS_ACCESS_KEY_ID=your_access_key_id_here
# AWS_SECRET_ACCESS_KEY=your_secret_access_key_here
```

The `.env` file will be automatically loaded when running deploy.py commands.

Option 2: Using AWS CLI
```bash
aws configure
```

Option 3: Environment variables
```bash
export AWS_ACCESS_KEY_ID=your_access_key_id
export AWS_SECRET_ACCESS_KEY=your_secret_access_key
```

## Configuration

Edit `config.yaml` to customize:
- AWS region
- VPC CIDR and subnets
- Instance type
- ASG capacity (min/desired/max)
- Scaling thresholds
- Environment name

## Usage

### Deploy Infrastructure

```bash
python deploy.py deploy --env dev
```

This will create:
- VPC with subnets and security groups
- Application Load Balancer
- Auto Scaling Group
- CloudWatch alarms
- EC2 instances running the metrics dashboard

### Check Status

```bash
python deploy.py status --env dev
```

Displays:
- ALB DNS name
- Target group health
- ASG size and instance count

### Destroy Infrastructure

```bash
python deploy.py destroy --env dev
```

Removes all resources in the correct order:
1. ASG (terminates instances)
2. CloudWatch alarms
3. ALB and Target Group
4. Security Groups
5. VPC and networking

## Application Endpoints

Once deployed, access the dashboard via the ALB DNS name:
- `GET /` - Real-time server metrics dashboard with WebSocket updates
- `GET /health` - Health check endpoint (returns 200 OK)
- `GET /api/metrics` - JSON API endpoint for system metrics

The dashboard displays:
- **Server Name** and **Instance ID** - Shows which server you're connected to
- **CPU Usage** - Live CPU percentage with progress bar
- **Memory Usage** - Memory percentage and used/total GB
- **Disk Usage** - Disk percentage and used/total GB

When you reload the page, the load balancer will route you to a different server, showing metrics from different instances in the ASG.

## Architecture

```
Internet → ALB → Target Group → ASG → EC2 Instances (Metrics Dashboard)
```

## State Management

The deployment state is stored in `state.db` (SQLite database) tracking:
- Deployment metadata
- All resource IDs (VPC, subnets, ALB, ASG, etc.)
- Resource metadata

This enables reliable destroy operations by querying the database for all created resources.

## Notes

- The deployment uses an existing EC2 Launch Template (`lt-0615440f322f6e574`)
- Security groups restrict EC2 access to ALB only
- Auto-scaling based on CPU utilization (scale-out at 50%, scale-in at 20%)
- The dashboard uses WebSockets for real-time updates (updates every second)
- Each page reload will show a different server instance due to load balancer routing

## Troubleshooting

- Ensure AWS credentials are configured correctly
- Verify the launch template exists in your AWS account
- Check CloudWatch logs for instance startup issues
- Verify security group rules allow ALB → EC2 communication
