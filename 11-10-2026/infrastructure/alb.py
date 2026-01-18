import boto3
import time
import random
import string
from botocore.exceptions import ClientError
from .state import add_resource, get_resource_by_type

def generate_suffix():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

def create_alb(elbv2_client, ec2_client, vpc_id: str, subnets: list, 
               alb_sg_id: str, environment: str, deployment_id: str) -> dict:
    try:
        existing_alb = get_resource_by_type(deployment_id, 'alb')
        if existing_alb:
            try:
                alb_info = elbv2_client.describe_load_balancers(
                    LoadBalancerArns=[existing_alb['resource_id']]
                )
                if alb_info['LoadBalancers']:
                    alb = alb_info['LoadBalancers'][0]
                    alb_dns = alb['DNSName']
                    
                    existing_tg = get_resource_by_type(deployment_id, 'target_group')
                    if existing_tg:
                        tg_arn = existing_tg['resource_id']
                        return {
                            'alb_arn': existing_alb['resource_id'],
                            'alb_dns': alb_dns,
                            'target_group_arn': tg_arn
                        }
            except ClientError:
                pass
        
        suffix = generate_suffix()
        alb_name = f"webapp-alb-{environment}-{suffix}"
        alb_arn = None
        alb_dns = None
        
        try:
            alb_response = elbv2_client.create_load_balancer(
                Name=alb_name,
                Subnets=subnets,
                SecurityGroups=[alb_sg_id],
                Scheme='internet-facing',
                Type='application',
                Tags=[
                    {'Key': 'Name', 'Value': f"webapp-alb-{environment}"}
                ]
            )
            
            alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
            alb_dns = alb_response['LoadBalancers'][0]['DNSName']
            
            add_resource(deployment_id, 'alb', alb_arn, alb_name, {'dns_name': alb_dns})
            
            time.sleep(10)
        except ClientError as e:
            if e.response['Error']['Code'] == 'DuplicateLoadBalancerName':
                try:
                    existing_albs = elbv2_client.describe_load_balancers(Names=[alb_name])
                    if existing_albs['LoadBalancers']:
                        alb = existing_albs['LoadBalancers'][0]
                        alb_arn = alb['LoadBalancerArn']
                        alb_dns = alb['DNSName']
                        add_resource(deployment_id, 'alb', alb_arn, alb_name, {'dns_name': alb_dns})
                    else:
                        raise
                except ClientError:
                    suffix = generate_suffix()
                    alb_name = f"webapp-alb-{environment}-{suffix}"
                    alb_response = elbv2_client.create_load_balancer(
                        Name=alb_name,
                        Subnets=subnets,
                        SecurityGroups=[alb_sg_id],
                        Scheme='internet-facing',
                        Type='application',
                        Tags=[
                            {'Key': 'Name', 'Value': f"webapp-alb-{environment}"}
                        ]
                    )
                    alb_arn = alb_response['LoadBalancers'][0]['LoadBalancerArn']
                    alb_dns = alb_response['LoadBalancers'][0]['DNSName']
                    add_resource(deployment_id, 'alb', alb_arn, alb_name, {'dns_name': alb_dns})
                    time.sleep(10)
            else:
                raise
        
        existing_tg = get_resource_by_type(deployment_id, 'target_group')
        if existing_tg:
            tg_arn = existing_tg['resource_id']
        else:
            tg_suffix = generate_suffix()
            tg_name = f"webapp-tg-{environment}-{tg_suffix}"
            
            try:
                tg_response = elbv2_client.create_target_group(
                    Name=tg_name,
                    Protocol='HTTP',
                    Port=80,
                    VpcId=vpc_id,
                    HealthCheckPath='/health',
                    HealthCheckProtocol='HTTP',
                    HealthCheckIntervalSeconds=30,
                    HealthCheckTimeoutSeconds=5,
                    HealthyThresholdCount=2,
                    UnhealthyThresholdCount=3,
                    TargetType='instance',
                    Tags=[
                        {'Key': 'Name', 'Value': f"webapp-tg-{environment}"}
                    ]
                )
                
                tg_arn = tg_response['TargetGroups'][0]['TargetGroupArn']
                add_resource(deployment_id, 'target_group', tg_arn, tg_name)
            except ClientError as e:
                if e.response['Error']['Code'] == 'DuplicateTargetGroupName':
                    existing_tgs = elbv2_client.describe_target_groups(Names=[tg_name])
                    if existing_tgs['TargetGroups']:
                        tg_arn = existing_tgs['TargetGroups'][0]['TargetGroupArn']
                        add_resource(deployment_id, 'target_group', tg_arn, tg_name)
                    else:
                        raise
                else:
                    raise
        
        existing_listener = get_resource_by_type(deployment_id, 'listener')
        if not existing_listener:
            try:
                listener_response = elbv2_client.create_listener(
                    LoadBalancerArn=alb_arn,
                    Protocol='HTTP',
                    Port=80,
                    DefaultActions=[
                        {
                            'Type': 'forward',
                            'TargetGroupArn': tg_arn
                        }
                    ]
                )
                
                listener_arn = listener_response['Listeners'][0]['ListenerArn']
                add_resource(deployment_id, 'listener', listener_arn)
            except ClientError as e:
                if e.response['Error']['Code'] != 'DuplicateListener':
                    raise
        
        return {
            'alb_arn': alb_arn,
            'alb_dns': alb_dns,
            'target_group_arn': tg_arn
        }
    except ClientError as e:
        raise Exception(f"Failed to create ALB: {e}")
