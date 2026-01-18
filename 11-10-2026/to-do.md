Scenario

You are a Platform Team building infrastructure for a simple Web application.

The application is an HTTP service that returns:
	•	GET /health → 200 OK
	•	GET / → text: “Hello from ”
	•	GET /assets/<file> → a static file stored in S3 (served via download or redirect)

The entire setup must be fully automated using Python (no manual AWS Console work), with the ability to deploy and destroy the environment.

⸻

Team Roles (recommended: 3–5 people per group)
	•	Infra Lead
Design VPC / Security Groups / ALB / ASG
	•	Automation Developer
Write Python automation (boto3 / AWS SDK)
	•	App / Bootstrapping
EC2 UserData / startup scripts, HTTP service, /health endpoint
	•	QA / Verifier
Validation, input/output checks, README, clean teardown

⸻

Mandatory Requirements (MVP)

1. S3 Bucket
	•	Create a uniquely named bucket (include a random suffix).
	•	Upload at least one file (e.g. logo.txt or index.html).
	•	Block Public Access must be enabled (bucket is NOT public).
	•	Access should be allowed only via the EC2 instance role.

⸻

2. Application Load Balancer (ALB)
	•	Listener on port 80
	•	HTTP Target Group
	•	Health check path: /health

⸻

3. Auto Scaling Group (ASG)
	•	Use a Launch Template (Launch Config is allowed but legacy).
	•	Launch EC2 instances.
	•	Capacity:
	•	Min = 1
	•	Desired = 2
	•	Max = 3
	•	Instances must be attached to the Target Group.
	•	Scaling policy:
	•	Either CPU-based (scale-out at ~50%, scale-in at ~20%)
	•	Or ALB RequestCountPerTarget

⸻

4. Application on EC2
	•	Provisioning via UserData (cloud-init / bash).
	•	Start an HTTP service:
	•	Can be python http.server with a custom handler, or Flask.
	•	/health must return 200 OK.
	•	S3 integration:
	•	Either download the file from S3 at startup
	•	Or expose an endpoint that serves or redirects to the S3 object

⸻

5. Automation Tooling

Provide a Python CLI:

python deploy.py deploy --env dev
python deploy.py status --env dev
python deploy.py destroy --env dev

Requirements:
	•	Use boto3
	•	Use a basic config file (YAML or JSON) for:
	•	AWS region
	•	VPC / subnets (or auto-create them)
	•	Instance type
	•	Desired capacity

⸻

Optional Extensions (choose 1–2)
	•	IAM Role for EC2 with minimal S3-only permissions (Least Privilege)
	•	Local state file (state.json) to support reliable destroy
	•	Full CloudWatch Alarms and Scaling Policies
	•	S3 versioning + lifecycle rules
	•	Basic Blue/Green deployment:
	•	New Target Group
	•	Switch ALB listener

⸻

4-Hour Implementation Plan (Class Timeline)

0:00–0:20 — Preparation
	•	Create AWS credentials
(recommended: dedicated IAM user or role for learning)
	•	Create Python virtual environment
	•	Install boto3
	•	Define region and naming convention

⸻

0:20–1:20 — Phase 1: S3 + App Bootstrap
	•	Create S3 bucket and upload asset
	•	Write UserData script to start HTTP service
	•	Ensure /health works
	•	Test locally or on a single EC2 instance (ALB not required yet)

Deliverable:
Working S3 bucket + functional UserData script

⸻

1:20–2:30 — Phase 2: ALB + Target Group + Security Groups
	•	Create ALB Security Group:
	•	Ingress: port 80 from the internet
	•	Create EC2 Security Group:
	•	Ingress: only from ALB Security Group
	•	Create:
	•	ALB
	•	Listener
	•	Target Group
	•	Health checks

Deliverable:
ALB running, Target Group ready

⸻

2:30–3:30 — Phase 3: ASG + Attach to Target Group
	•	Create Launch Template:
	•	Includes UserData
	•	Includes Instance Profile (IAM Role)
	•	Create Auto Scaling Group
	•	Attach ASG to Target Group
	•	Set Desired = 2
	•	Verify both instances are Healthy

Deliverable:
ALB DNS responds and traffic is distributed between instances

⸻

3:30–4:00 — Phase 4: Validation + Destroy
	•	status command should print:
	•	ALB DNS name
	•	Target Group health
	•	ASG size
	•	S3 bucket name
	•	destroy command must delete resources in correct order:
	1.	ASG
	2.	Launch Template
	3.	ALB & Target Group
	4.	Security Groups & IAM
	5.	S3 bucket

Final Requirement:
Clean teardown with no dangling resources
