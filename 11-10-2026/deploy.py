#!/usr/bin/env python3
import argparse
import yaml
import boto3
import uuid
import time
import os
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from infrastructure.state import (
    init_db, create_deployment, update_deployment_status,
    get_deployment_id, get_resources, delete_deployment, get_resource_by_type,
    get_all_deployments
)
from infrastructure.vpc import create_vpc
from infrastructure.alb import create_alb
from infrastructure.asg import create_asg
from infrastructure.cloudwatch import create_scaling_policies
from infrastructure.ssh import get_ssh_key_file

def load_env():
    load_dotenv()
    aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    
    if aws_access_key_id and aws_secret_access_key:
        os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key_id
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_access_key
        return True
    return False

def get_boto3_session(region_name):
    aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    
    if aws_access_key_id and aws_secret_access_key:
        return boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )
    return boto3.Session(region_name=region_name)

def load_config(config_path='config.yaml'):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def deploy(config, environment):
    print(f"Deploying infrastructure for environment: {environment}")
    
    init_db()
    deployment_id = str(uuid.uuid4())
    create_deployment(environment, deployment_id)
    
    region = config['aws']['region']
    session = get_boto3_session(region)
    
    ec2_client = session.client('ec2')
    elbv2_client = session.client('elbv2')
    autoscaling_client = session.client('autoscaling')
    cloudwatch_client = session.client('cloudwatch')
    
    try:
        print("Creating VPC and networking...")
        vpc_config = config['vpc'].copy()
        vpc_info = create_vpc(ec2_client, vpc_config, environment, deployment_id)
        print(f"VPC created: {vpc_info['vpc_id']}")
        
        print("Creating ALB and Target Group...")
        alb_info = create_alb(
            elbv2_client, ec2_client, vpc_info['vpc_id'],
            vpc_info['subnets'], vpc_info['alb_sg_id'], environment, deployment_id
        )
        print(f"ALB created: {alb_info['alb_dns']}")
        
        print("Creating Auto Scaling Group...")
        asg_config = config['asg'].copy()
        instance_config = config['instance']
        asg_name = create_asg(
            autoscaling_client, ec2_client, elbv2_client, alb_info['target_group_arn'],
            vpc_info['subnets'], vpc_info['ec2_sg_id'], asg_config, instance_config,
            environment, deployment_id, region
        )
        print(f"ASG created: {asg_name}")
        
        print("Creating CloudWatch alarms and scaling policies...")
        create_scaling_policies(
            cloudwatch_client, autoscaling_client, asg_name,
            config['scaling']['scale_out_threshold'],
            config['scaling']['scale_in_threshold'],
            environment, deployment_id
        )
        print("CloudWatch alarms created")
        
        update_deployment_status(deployment_id, 'completed')
        print(f"\nDeployment completed successfully!")
        print(f"ALB DNS: {alb_info['alb_dns']}")
        print(f"Deployment ID: {deployment_id}")
        
    except Exception as e:
        update_deployment_status(deployment_id, 'failed')
        print(f"Deployment failed: {e}")
        raise

def ssh_get_logs(instance_id: str, private_ip: str, key_file: str):
    import subprocess
    
    commands = [
        'echo "=== Systemd Service Status ==="',
        'sudo systemctl status webapp.service --no-pager -l || echo "Service not found"',
        'echo ""',
        'echo "=== Recent Application Logs (last 50 lines) ==="',
        'sudo journalctl -u webapp.service -n 50 --no-pager || echo "No logs found"',
        'echo ""',
        'echo "=== Health Check Test ==="',
        'curl -s http://localhost/health || echo "Health check failed"',
        'echo ""',
        'echo "=== Port 80 Status ==="',
        'sudo netstat -tlnp | grep :80 || sudo ss -tlnp | grep :80 || echo "Port 80 not listening"',
        'echo ""',
        'echo "=== Python Process Check ==="',
        'ps aux | grep python3 | grep app.py || echo "App process not running"',
        'echo ""',
        'echo "=== UserData Log (last 50 lines) ==="',
        'tail -50 /var/log/user-data.log 2>/dev/null || echo "UserData log not found"',
        'echo ""',
        'echo "=== Python Dependencies ==="',
        'pip3 list | grep -E "flask|socketio|psutil" || echo "Dependencies not found"'
    ]
    
    try:
        cmd_str = '; '.join(commands)
        ssh_cmd = [
            'ssh',
            '-i', key_file,
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=10',
            f'ec2-user@{private_ip}',
            cmd_str
        ]
        
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return None, "SSH connection timed out"
    except FileNotFoundError:
        return None, "SSH command not found"
    except Exception as e:
        return None, str(e)

def get_cloudwatch_logs(logs_client, log_group: str, instance_id: str):
    try:
        log_streams = logs_client.describe_log_streams(
            logGroupName=log_group,
            logStreamNamePrefix=instance_id,
            orderBy='LastEventTime',
            descending=True,
            limit=5
        )
        
        all_logs = []
        for stream in log_streams.get('logStreams', []):
            stream_name = stream['logStreamName']
            try:
                events = logs_client.get_log_events(
                    logGroupName=log_group,
                    logStreamName=stream_name,
                    limit=100,
                    startFromHead=False
                )
                for event in events.get('events', []):
                    timestamp = event['timestamp']
                    message = event['message']
                    all_logs.append((timestamp, stream_name, message))
            except Exception:
                continue
        
        if all_logs:
            all_logs.sort(key=lambda x: x[0], reverse=True)
            return '\n'.join([f"[{s}] {m}" for _, s, m in all_logs[:100]])
        return None
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
            return None
        return None
    except Exception:
        return None

def get_instance_logs(ssm_client, instance_id: str):
    try:
        response = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                'commands': [
                    'echo "=== Systemd Service Status ==="',
                    'sudo systemctl status webapp.service --no-pager -l || echo "Service not found"',
                    'echo ""',
                    'echo "=== Recent Application Logs (last 50 lines) ==="',
                    'sudo journalctl -u webapp.service -n 50 --no-pager || echo "No logs found"',
                    'echo ""',
                    'echo "=== Health Check Test ==="',
                    'curl -s http://localhost/health || echo "Health check failed"',
                    'echo ""',
                    'echo "=== Port 80 Status ==="',
                    'sudo netstat -tlnp | grep :80 || sudo ss -tlnp | grep :80 || echo "Port 80 not listening"',
                    'echo ""',
                    'echo "=== Python Process Check ==="',
                    'ps aux | grep python3 | grep app.py || echo "App process not running"',
                    'echo ""',
                    'echo "=== UserData Log ==="',
                    'tail -50 /var/log/user-data.log 2>/dev/null || echo "UserData log not found"'
                ]
            }
        )
        command_id = response['Command']['CommandId']
        
        import time
        max_wait = 15
        waited = 0
        while waited < max_wait:
            time.sleep(2)
            waited += 2
            try:
                output = ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id
                )
                status = output.get('Status', '')
                if status in ['Success', 'Failed', 'TimedOut', 'Cancelled']:
                    return output.get('StandardOutputContent', ''), output.get('StandardErrorContent', '')
            except ClientError:
                pass
        
        output = ssm_client.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id
        )
        return output.get('StandardOutputContent', ''), output.get('StandardErrorContent', '')
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code in ['InvalidInstanceId', 'InvalidInstance']:
            return None, "SSM agent not available or instance not registered with SSM"
        return None, str(e)
    except Exception as e:
        return None, str(e)

def status(config, environment):
    init_db()
    deployment_id = get_deployment_id(environment)
    
    if not deployment_id:
        print(f"No deployment found for environment: {environment}")
        return
    
    resources = get_resources(deployment_id)
    
    region = config['aws']['region']
    session = get_boto3_session(region)
    elbv2_client = session.client('elbv2')
    autoscaling_client = session.client('autoscaling')
    ec2_client = session.client('ec2')
    ssm_client = session.client('ssm')
    logs_client = session.client('logs')
    
    alb_resource = get_resource_by_type(deployment_id, 'alb')
    tg_resource = get_resource_by_type(deployment_id, 'target_group')
    asg_resource = get_resource_by_type(deployment_id, 'asg')
    
    print(f"\nDeployment Status for environment: {environment}")
    print(f"Deployment ID: {deployment_id}")
    print("=" * 70)
    
    if alb_resource:
        alb_dns = alb_resource.get('metadata', {}).get('dns_name', 'N/A')
        print(f"ALB DNS: {alb_dns}")
    
    if tg_resource:
        try:
            tg_health = elbv2_client.describe_target_health(
                TargetGroupArn=tg_resource['resource_id']
            )
            healthy = sum(1 for t in tg_health['TargetHealthDescriptions'] 
                         if t['TargetHealth']['State'] == 'healthy')
            total = len(tg_health['TargetHealthDescriptions'])
            print(f"\nTarget Group Health: {healthy}/{total} healthy")
            
            if total > 0:
                print("\nTarget Health Details:")
                for target in tg_health['TargetHealthDescriptions']:
                    state = target['TargetHealth']['State']
                    reason = target['TargetHealth'].get('Reason', 'N/A')
                    description = target['TargetHealth'].get('Description', 'N/A')
                    instance_id = target['Target']['Id']
                    print(f"  Instance {instance_id}: {state}")
                    if state != 'healthy':
                        print(f"    Reason: {reason}")
                        print(f"    Description: {description}")
        except Exception as e:
            print(f"Target Group Health: Error - {e}")
    
    if asg_resource:
        try:
            asg_info = autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_resource['resource_id']]
            )
            if asg_info['AutoScalingGroups']:
                asg = asg_info['AutoScalingGroups'][0]
                print(f"\nASG Size: Min={asg['MinSize']}, Desired={asg['DesiredCapacity']}, Max={asg['MaxSize']}")
                print(f"ASG Instances: {len(asg['Instances'])}")
                
                if asg['Instances']:
                    print("\n" + "=" * 70)
                    print("INSTANCE LOGS AND DEBUG INFO")
                    print("=" * 70)
                    
                    instance_ids = [inst['InstanceId'] for inst in asg['Instances']]
                    
                    for instance_id in instance_ids:
                        try:
                            instance_info = ec2_client.describe_instances(InstanceIds=[instance_id])
                            instance = instance_info['Reservations'][0]['Instances'][0]
                            state = instance['State']['Name']
                            launch_time = instance['LaunchTime']
                            private_ip = instance.get('PrivateIpAddress', 'N/A')
                            public_ip = instance.get('PublicIpAddress', 'N/A')
                            
                            print(f"\n{'='*70}")
                            print(f"Instance: {instance_id}")
                            print(f"  State: {state}")
                            print(f"  Private IP: {private_ip}")
                            print(f"  Public IP: {public_ip}")
                            print(f"  Launch Time: {launch_time}")
                            print(f"{'='*70}")
                            
                            if state == 'running':
                                print(f"\nFetching logs from {instance_id}...")
                                
                                log_group = f"/aws/ec2/webapp-{environment}"
                                cw_logs = get_cloudwatch_logs(logs_client, log_group, instance_id)
                                
                                if cw_logs:
                                    print("\n--- CloudWatch Logs (Recent) ---")
                                    print(cw_logs)
                                    print("")
                                
                                stdout, stderr = get_instance_logs(ssm_client, instance_id)
                                
                                if stdout:
                                    print("--- SSM Command Output ---")
                                    print(stdout)
                                    print("")
                                
                                key_file = get_ssh_key_file(environment)
                                # Use public IP if available, otherwise fall back to private IP
                                ssh_ip = public_ip if public_ip != 'N/A' else private_ip
                                
                                if key_file and ssh_ip != 'N/A':
                                    print("--- SSH Log Fetch ---")
                                    ssh_stdout, ssh_stderr = ssh_get_logs(instance_id, ssh_ip, key_file)
                                    
                                    if ssh_stdout:
                                        print(ssh_stdout)
                                    elif ssh_stderr:
                                        print(f"SSH Error: {ssh_stderr}")
                                        print(f"  Trying to SSH to: ec2-user@{ssh_ip}")
                                        print(f"  Using key: {key_file}")
                                elif not key_file:
                                    print("  ⚠️  SSH key file not found. Run deploy to generate SSH keys.")
                                
                                if not cw_logs and not stdout and not (key_file and ssh_ip != 'N/A'):
                                    print("\n  ⚠️  Could not fetch logs via SSM, CloudWatch, or SSH")
                                    print("\n  Manual Debugging Steps (SSH into instance):")
                                    if public_ip != 'N/A':
                                        print(f"    ssh -i webapp-key-{environment}.pem ec2-user@{public_ip}")
                                    else:
                                        print(f"    ssh -i webapp-key-{environment}.pem ec2-user@{private_ip}")
                                    print("  1. Check service status:")
                                    print("     sudo systemctl status webapp.service")
                                    print("  2. Check application logs:")
                                    print("     sudo journalctl -u webapp.service -n 50")
                                    print("  3. Test health endpoint:")
                                    print("     curl http://localhost/health")
                                    print("  4. Check if port 80 is listening:")
                                    print("     sudo netstat -tlnp | grep :80")
                                    print("  5. Check UserData script logs:")
                                    print("     tail -50 /var/log/user-data.log")
                                    print("  6. Check Python dependencies:")
                                    print("     pip3 list | grep -E 'flask|socketio|psutil'")
                                    print("  7. Check if app file exists:")
                                    print("     ls -la /opt/webapp/app.py")
                                    print("  8. Try restarting service:")
                                    print("     sudo systemctl restart webapp.service")
                                
                                if stderr and 'SSM agent not available' not in stderr:
                                    print(f"  SSM Error: {stderr}")
                            else:
                                print(f"  Instance is {state}, cannot fetch logs")
                        except Exception as e:
                            print(f"  Error fetching info for {instance_id}: {e}")
        except Exception as e:
            print(f"ASG Info: Error - {e}")
    
    print("\n" + "=" * 70)

def destroy(config, environment):
    print(f"Destroying infrastructure for environment: {environment}")
    
    init_db()
    deployment_id = get_deployment_id(environment)
    
    if not deployment_id:
        print(f"No deployment found for environment: {environment}")
        return
    
    resources = get_resources(deployment_id)
    region = config['aws']['region']
    session = get_boto3_session(region)
    
    ec2_client = session.client('ec2')
    elbv2_client = session.client('elbv2')
    autoscaling_client = session.client('autoscaling')
    cloudwatch_client = session.client('cloudwatch')
    
    try:
        asg_resources = [r for r in resources if r['resource_type'] == 'asg']
        asg_name = asg_resources[0]['resource_id'] if asg_resources else None
        
        scaling_policy_resources = [r for r in resources if r['resource_type'] == 'scaling_policy']
        for policy in scaling_policy_resources:
            try:
                if asg_name:
                    print(f"Deleting scaling policy: {policy['resource_name']}")
                    autoscaling_client.delete_policy(
                        PolicyName=policy['resource_name'],
                        AutoScalingGroupName=asg_name
                    )
            except Exception as e:
                print(f"Error deleting policy {policy['resource_id']}: {e}")
        
        alarm_resources = [r for r in resources if r['resource_type'] == 'cloudwatch_alarm']
        for alarm in alarm_resources:
            try:
                print(f"Deleting CloudWatch alarm: {alarm['resource_id']}")
                cloudwatch_client.delete_alarms(AlarmNames=[alarm['resource_id']])
            except Exception as e:
                print(f"Error deleting alarm {alarm['resource_id']}: {e}")
        
        if asg_resources:
            asg_name = asg_resources[0]['resource_id']
            print(f"Deleting ASG: {asg_name}")
            autoscaling_client.update_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=0,
                DesiredCapacity=0
            )
            
            while True:
                asg_info = autoscaling_client.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[asg_name]
                )
                if not asg_info['AutoScalingGroups'] or len(asg_info['AutoScalingGroups'][0]['Instances']) == 0:
                    break
                print("Waiting for instances to terminate...")
                time.sleep(10)
            
            autoscaling_client.delete_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                ForceDelete=True
            )
            print("ASG deleted")
            time.sleep(10)
        
        lt_resources = [r for r in resources if r['resource_type'] == 'launch_template']
        for lt in lt_resources:
            try:
                print(f"Deleting launch template: {lt['resource_id']}")
                ec2_client.delete_launch_template(LaunchTemplateId=lt['resource_id'])
            except Exception as e:
                print(f"Error deleting launch template: {e}")
        
        listener_resources = [r for r in resources if r['resource_type'] == 'listener']
        for listener in listener_resources:
            try:
                print(f"Deleting listener: {listener['resource_id']}")
                elbv2_client.delete_listener(ListenerArn=listener['resource_id'])
            except Exception as e:
                print(f"Error deleting listener: {e}")
        
        tg_resources = [r for r in resources if r['resource_type'] == 'target_group']
        for tg in tg_resources:
            try:
                print(f"Deleting target group: {tg['resource_id']}")
                elbv2_client.delete_target_group(TargetGroupArn=tg['resource_id'])
            except Exception as e:
                print(f"Error deleting target group: {e}")
        
        alb_resources = [r for r in resources if r['resource_type'] == 'alb']
        for alb in alb_resources:
            try:
                print(f"Deleting ALB: {alb['resource_id']}")
                elbv2_client.delete_load_balancer(LoadBalancerArn=alb['resource_id'])
                print("Waiting for ALB to fully delete...")
                max_wait = 30
                waited = 0
                while waited < max_wait:
                    try:
                        elbv2_client.describe_load_balancers(LoadBalancerArns=[alb['resource_id']])
                        time.sleep(5)
                        waited += 5
                    except ClientError:
                        break
                time.sleep(10)
            except Exception as e:
                print(f"Error deleting ALB: {e}")
        
        print("Waiting for network interfaces to be released...")
        time.sleep(20)
        
        sg_resources = [r for r in resources if r['resource_type'] == 'security_group']
        for sg in sg_resources:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Deleting security group: {sg['resource_id']}")
                    ec2_client.delete_security_group(GroupId=sg['resource_id'])
                    break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation' and attempt < max_retries - 1:
                        print(f"Security group has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(10)
                    else:
                        print(f"Error deleting security group: {e}")
                        break
        
        rt_resources = [r for r in resources if r['resource_type'] == 'route_table']
        vpc_resources = [r for r in resources if r['resource_type'] == 'vpc']
        vpc_id = vpc_resources[0]['resource_id'] if vpc_resources else None
        
        if vpc_id:
            try:
                network_interfaces = ec2_client.describe_network_interfaces(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )
                if network_interfaces['NetworkInterfaces']:
                    print("Waiting for network interfaces to be released...")
                    max_wait = 60
                    waited = 0
                    while waited < max_wait and network_interfaces['NetworkInterfaces']:
                        time.sleep(5)
                        waited += 5
                        network_interfaces = ec2_client.describe_network_interfaces(
                            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                        )
            except Exception as e:
                print(f"Error checking network interfaces: {e}")
        
        for rt in rt_resources:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Deleting route table: {rt['resource_id']}")
                    ec2_client.delete_route_table(RouteTableId=rt['resource_id'])
                    break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation' and attempt < max_retries - 1:
                        print(f"Route table has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(10)
                    else:
                        print(f"Error deleting route table: {e}")
                        break
        
        subnet_resources = [r for r in resources if r['resource_type'] == 'subnet']
        for subnet in subnet_resources:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Deleting subnet: {subnet['resource_id']}")
                    ec2_client.delete_subnet(SubnetId=subnet['resource_id'])
                    break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation' and attempt < max_retries - 1:
                        print(f"Subnet has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(10)
                    else:
                        print(f"Error deleting subnet: {e}")
                        break
        
        igw_resources = [r for r in resources if r['resource_type'] == 'internet_gateway']
        
        for igw in igw_resources:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    vpc_id = vpc_resources[0]['resource_id'] if vpc_resources else None
                    if vpc_id:
                        try:
                            ec2_client.detach_internet_gateway(
                                InternetGatewayId=igw['resource_id'],
                                VpcId=vpc_id
                            )
                        except ClientError as e:
                            if e.response['Error']['Code'] != 'Gateway.NotAttached':
                                if attempt < max_retries - 1:
                                    print(f"Internet gateway has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                                    time.sleep(10)
                                    continue
                                else:
                                    raise
                    print(f"Deleting internet gateway: {igw['resource_id']}")
                    ec2_client.delete_internet_gateway(InternetGatewayId=igw['resource_id'])
                    break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation' and attempt < max_retries - 1:
                        print(f"Internet gateway has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(10)
                    else:
                        print(f"Error deleting internet gateway: {e}")
                        break
        
        for vpc in vpc_resources:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Deleting VPC: {vpc['resource_id']}")
                    ec2_client.delete_vpc(VpcId=vpc['resource_id'])
                    break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DependencyViolation' and attempt < max_retries - 1:
                        print(f"VPC has dependencies, waiting... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(15)
                    else:
                        print(f"Error deleting VPC: {e}")
                        break
        
        delete_deployment(deployment_id)
        print("\nDestroy completed successfully!")
        
    except Exception as e:
        print(f"Destroy failed: {e}")
        raise

def destroy_vpc_and_resources(ec2_client, vpc_id: str, region: str, session=None):
    try:
        if session is None:
            import boto3
            session = boto3.Session()
        
        elbv2_client = session.client('elbv2', region_name=region)
        
        print(f"  Destroying VPC: {vpc_id}")
        
        albs = elbv2_client.describe_load_balancers()
        vpc_albs = [alb for alb in albs.get('LoadBalancers', []) if alb['VpcId'] == vpc_id]
        
        if vpc_albs:
            print(f"    Found {len(vpc_albs)} ALB(s) in VPC, deleting them first...")
            for alb in vpc_albs:
                try:
                    print(f"      Deleting ALB: {alb['LoadBalancerName']} ({alb['LoadBalancerArn']})")
                    elbv2_client.delete_load_balancer(LoadBalancerArn=alb['LoadBalancerArn'])
                    print(f"      ✓ ALB deletion initiated")
                except ClientError as e:
                    print(f"      Error deleting ALB: {e}")
            
            print(f"    Waiting for ALBs to be deleted...")
            time.sleep(30)
            
            target_groups = elbv2_client.describe_target_groups()
            vpc_tgs = [tg for tg in target_groups.get('TargetGroups', []) if tg['VpcId'] == vpc_id]
            
            for tg in vpc_tgs:
                try:
                    print(f"      Deleting target group: {tg['TargetGroupName']}")
                    elbv2_client.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
                except ClientError as e:
                    print(f"      Error deleting target group: {e}")
        
        subnets = ec2_client.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )['Subnets']
        
        for subnet in subnets:
            try:
                print(f"    Deleting subnet: {subnet['SubnetId']}")
                ec2_client.delete_subnet(SubnetId=subnet['SubnetId'])
            except ClientError as e:
                if e.response['Error']['Code'] != 'DependencyViolation':
                    print(f"    Error deleting subnet: {e}")
        
        route_tables = ec2_client.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )['RouteTables']
        
        for rt in route_tables:
            if not rt.get('Associations') or not any(a.get('Main', False) for a in rt['Associations']):
                try:
                    print(f"    Deleting route table: {rt['RouteTableId']}")
                    ec2_client.delete_route_table(RouteTableId=rt['RouteTableId'])
                except ClientError as e:
                    if e.response['Error']['Code'] != 'DependencyViolation':
                        print(f"    Error deleting route table: {e}")
        
        security_groups = ec2_client.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )['SecurityGroups']
        
        for sg in security_groups:
            if sg['GroupName'] != 'default':
                try:
                    print(f"    Deleting security group: {sg['GroupId']}")
                    ec2_client.delete_security_group(GroupId=sg['GroupId'])
                except ClientError as e:
                    if e.response['Error']['Code'] != 'DependencyViolation':
                        print(f"    Error deleting security group: {e}")
        
        internet_gateways = ec2_client.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
        )['InternetGateways']
        
        for igw in internet_gateways:
            try:
                print(f"    Detaching internet gateway: {igw['InternetGatewayId']}")
                ec2_client.detach_internet_gateway(
                    InternetGatewayId=igw['InternetGatewayId'],
                    VpcId=vpc_id
                )
                print(f"    Deleting internet gateway: {igw['InternetGatewayId']}")
                ec2_client.delete_internet_gateway(InternetGatewayId=igw['InternetGatewayId'])
            except ClientError as e:
                if e.response['Error']['Code'] not in ['DependencyViolation', 'Gateway.NotAttached']:
                    print(f"    Error deleting internet gateway: {e}")
        
        time.sleep(10)
        
        network_interfaces = ec2_client.describe_network_interfaces(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )['NetworkInterfaces']
        
        if network_interfaces:
            print(f"    Found {len(network_interfaces)} network interface(s), checking attachments...")
            
            for ni in network_interfaces:
                ni_id = ni['NetworkInterfaceId']
                attachment = ni.get('Attachment', {})
                status = ni.get('Status', 'unknown')
                description = ni.get('Description', '')
                print(f"      Network interface {ni_id} - Status: {status}, Description: {description}")
                
                if attachment:
                    attachment_id = attachment.get('AttachmentId')
                    instance_id = attachment.get('InstanceId')
                    attachment_status = attachment.get('Status', 'unknown')
                    print(f"        Attached to: {instance_id or 'unknown'} (status: {attachment_status})")
                    
                    if instance_id:
                        try:
                            instance_info = ec2_client.describe_instances(InstanceIds=[instance_id])
                            instance_state = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
                            print(f"        Instance state: {instance_state}")
                            
                            if instance_state not in ['terminated', 'shutting-down']:
                                print(f"        Terminating instance {instance_id}...")
                                try:
                                    ec2_client.terminate_instances(InstanceIds=[instance_id])
                                    print(f"        ✓ Instance termination initiated")
                                    print(f"        Waiting for instance to terminate...")
                                    waiter = ec2_client.get_waiter('instance_terminated')
                                    waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
                                    print(f"        ✓ Instance terminated")
                                except ClientError as e:
                                    print(f"        Could not terminate instance: {e}")
                            else:
                                print(f"        Instance already {instance_state}")
                        except ClientError as e:
                            if 'InvalidInstanceID.NotFound' in str(e):
                                print(f"        Instance not found, proceeding to delete network interface...")
                            else:
                                print(f"        Error checking instance: {e}")
                
                try:
                    print(f"        Attempting to delete network interface {ni_id}...")
                    ec2_client.delete_network_interface(NetworkInterfaceId=ni_id)
                    print(f"        ✓ Network interface deleted")
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', '')
                    if error_code == 'InvalidNetworkInterfaceID.NotFound':
                        print(f"        Already deleted")
                    elif error_code == 'InvalidParameterValue':
                        print(f"        Network interface still attached, waiting...")
                        time.sleep(10)
                        try:
                            ec2_client.delete_network_interface(NetworkInterfaceId=ni_id)
                            print(f"        ✓ Network interface deleted after wait")
                        except ClientError:
                            print(f"        Could not delete network interface: {e}")
                    else:
                        print(f"        Could not delete network interface: {e}")
            
            print(f"    Waiting for network interface(s) to be released (max 30 seconds)...")
            max_wait = 30
            waited = 0
            check_interval = 3
            while waited < max_wait:
                remaining = ec2_client.describe_network_interfaces(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )['NetworkInterfaces']
                if not remaining:
                    print(f"    All network interfaces released!")
                    break
                if waited % 9 == 0 and waited > 0:
                    print(f"    Still waiting... ({waited}/{max_wait}s) - {len(remaining)} interface(s) remaining")
                time.sleep(check_interval)
                waited += check_interval
            
            if waited >= max_wait:
                remaining = ec2_client.describe_network_interfaces(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )['NetworkInterfaces']
                if remaining:
                    print(f"    Warning: {len(remaining)} network interface(s) still exist after timeout")
                    print(f"    Proceeding with VPC deletion anyway (AWS may handle cleanup)...")
                    for ni in remaining:
                        print(f"      - {ni['NetworkInterfaceId']} (status: {ni.get('Status', 'unknown')})")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"    Attempting to delete VPC: {vpc_id} (attempt {attempt + 1}/{max_retries})")
                ec2_client.delete_vpc(VpcId=vpc_id)
                print(f"  ✓ VPC {vpc_id} deleted successfully")
                return True
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'DependencyViolation' and attempt < max_retries - 1:
                    remaining_nis = ec2_client.describe_network_interfaces(
                        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                    )['NetworkInterfaces']
                    if remaining_nis:
                        print(f"    VPC still has {len(remaining_nis)} network interface(s), trying to delete them...")
                        for ni in remaining_nis:
                            try:
                                if ni.get('Attachment', {}).get('AttachmentId'):
                                    ec2_client.detach_network_interface(
                                        AttachmentId=ni['Attachment']['AttachmentId'],
                                        Force=True
                                    )
                                    time.sleep(2)
                                ec2_client.delete_network_interface(NetworkInterfaceId=ni['NetworkInterfaceId'])
                            except ClientError:
                                pass
                    print(f"    Waiting before retry... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(10)
                else:
                    print(f"  ✗ Could not delete VPC {vpc_id}: {e}")
                    print(f"    Skipping this VPC and continuing...")
                    return False
    except Exception as e:
        print(f"  ✗ Error destroying VPC {vpc_id}: {e}")
        print(f"    Skipping this VPC and continuing...")
        return False

def destroy_all(config):
    print("Destroying ALL deployments and VPCs in account...")
    
    region = config['aws']['region']
    session = get_boto3_session(region)
    ec2_client = session.client('ec2')
    
    init_db()
    all_deployments = get_all_deployments()
    
    tracked_vpc_ids = set()
    if all_deployments:
        print(f"\nFound {len(all_deployments)} tracked deployment(s):")
        for dep in all_deployments:
            print(f"  - {dep['environment']}: {dep['deployment_id']} ({dep['status']})")
            vpc_resource = get_resource_by_type(dep['deployment_id'], 'vpc')
            if vpc_resource:
                tracked_vpc_ids.add(vpc_resource['resource_id'])
    
    all_vpcs = ec2_client.describe_vpcs()['Vpcs']
    all_vpc_ids = [vpc['VpcId'] for vpc in all_vpcs if vpc['VpcId'] != 'default']
    
    print(f"\nFound {len(all_vpc_ids)} VPC(s) in account:")
    for vpc_id in all_vpc_ids:
        vpc_info = next((v for v in all_vpcs if v['VpcId'] == vpc_id), None)
        vpc_name = next((tag['Value'] for tag in vpc_info.get('Tags', []) if tag['Key'] == 'Name'), 'unnamed')
        tracked = " (tracked)" if vpc_id in tracked_vpc_ids else " (not tracked)"
        print(f"  - {vpc_id}: {vpc_name}{tracked}")
    
    if not all_deployments and not all_vpc_ids:
        print("\nNo deployments or VPCs found to destroy.")
        return
    
    print("\n⚠️  WARNING: This will destroy ALL VPCs in your AWS account!")
    print("   This includes VPCs NOT created by this tool!")
    confirm = input("\nAre you absolutely sure? Type 'yes' to confirm: ")
    if confirm.lower() != 'yes':
        print("Destroy cancelled.")
        return
    
    if all_deployments:
        environments = set(dep['environment'] for dep in all_deployments)
        for env in sorted(environments):
            print(f"\n{'='*60}")
            print(f"Destroying tracked environment: {env}")
            print(f"{'='*60}")
            try:
                destroy(config, env)
            except Exception as e:
                print(f"Error destroying {env}: {e}")
                continue
    
    if all_vpc_ids:
        print(f"\n{'='*60}")
        print(f"Destroying ALL VPCs in account ({len(all_vpc_ids)} VPCs)")
        print(f"{'='*60}")
        
        failed_vpcs = []
        for vpc_id in all_vpc_ids:
            if vpc_id not in tracked_vpc_ids:
                print(f"\nDestroying untracked VPC: {vpc_id}")
                success = destroy_vpc_and_resources(ec2_client, vpc_id, region, session)
                if not success:
                    failed_vpcs.append(vpc_id)
        
        if failed_vpcs:
            print(f"\n{len(failed_vpcs)} VPC(s) could not be deleted:")
            for vpc_id in failed_vpcs:
                print(f"  - {vpc_id}")
            print("\nThese VPCs may have active resources. Check AWS Console to manually clean them up.")
        
        time.sleep(5)
        
        remaining_vpcs = ec2_client.describe_vpcs()['Vpcs']
        remaining_vpc_ids = [v['VpcId'] for v in remaining_vpcs if v['VpcId'] != 'default' and v['VpcId'] not in failed_vpcs]
        
        if remaining_vpc_ids:
            print(f"\nRetrying deletion of {len(remaining_vpc_ids)} remaining VPC(s)...")
            for vpc_id in remaining_vpc_ids:
                success = destroy_vpc_and_resources(ec2_client, vpc_id, region)
                if not success and vpc_id not in failed_vpcs:
                    failed_vpcs.append(vpc_id)
    
    print(f"\n{'='*60}")
    print("Destroy all completed!")
    print(f"{'='*60}")

def main():
    load_env()
    
    parser = argparse.ArgumentParser(description='AWS Infrastructure Deployment Tool')
    parser.add_argument('command', choices=['deploy', 'status', 'destroy', 'destroy-all'],
                       help='Command to execute')
    parser.add_argument('--env', '--environment', dest='env', required=False,
                       help='Environment name (dev/staging/prod) - required for deploy/status/destroy')
    parser.add_argument('--config', default='config.yaml',
                       help='Path to config file (default: config.yaml)')
    
    args = parser.parse_args()
    
    if args.command != 'destroy-all' and not args.env:
        parser.error("--env is required for deploy, status, and destroy commands")
    
    config = load_config(args.config)
    
    if args.command == 'deploy':
        deploy(config, args.env)
    elif args.command == 'status':
        status(config, args.env)
    elif args.command == 'destroy':
        destroy(config, args.env)
    elif args.command == 'destroy-all':
        destroy_all(config)

if __name__ == '__main__':
    main()
