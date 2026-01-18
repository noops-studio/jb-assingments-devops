# Troubleshooting 502 Bad Gateway

## Common Causes

1. **Instances Still Starting** - EC2 instances need 2-3 minutes to:
   - Boot up
   - Install Python, pip, and dependencies
   - Start the Flask application
   - Pass health checks

2. **Health Check Failing** - The target group health check might be failing if:
   - The app isn't running on port 80
   - The `/health` endpoint isn't responding
   - Security groups aren't configured correctly

3. **Security Group Issues** - ALB can't reach EC2 instances if:
   - EC2 security group doesn't allow traffic from ALB security group
   - Port 80 isn't open

## How to Check

### 1. Check Target Group Health
```bash
# Get the target group ARN from status
python deploy.py status --env dev

# Then check health
aws elbv2 describe-target-health --target-group-arn <TARGET_GROUP_ARN>
```

### 2. Check Instance Status
```bash
# SSH into an instance and check:
sudo systemctl status webapp.service
sudo journalctl -u webapp.service -f
curl http://localhost/health
```

### 3. Check Security Groups
- ALB Security Group: Should allow inbound port 80 from 0.0.0.0/0
- EC2 Security Group: Should allow inbound port 80 from ALB Security Group

### 4. Wait for Instances
After deployment, wait 3-5 minutes for:
- Instances to launch
- Packages to install
- App to start
- Health checks to pass

## Quick Fixes

1. **Wait longer** - Give instances 3-5 minutes after deployment
2. **Check logs** - SSH into instance and check systemd logs
3. **Restart service** - `sudo systemctl restart webapp.service`
4. **Verify health endpoint** - `curl http://localhost/health` should return 200
