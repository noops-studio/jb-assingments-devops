#!/usr/bin/env python3
import boto3
import yaml
import json
import argparse
import sys
import time
from botocore.exceptions import ClientError

class InfrastructureDeployer:
    def __init__(self, config_path, env):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.env = env
        self.region = self.config['region']
        self.session = boto3.Session(region_name=self.region)
        self.ec2 = self.session.client('ec2')
        self.elbv2 = self.session.client('elbv2')
        self.autoscaling = self.session.client('autoscaling')
        self.cloudwatch = self.session.client('cloudwatch')
        self.state_file = 'state.json'
        self.state = self.load_state()
        
    def load_state(self):
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f).get(self.env, {})
        except FileNotFoundError:
            return {}
    
    def save_state(self):
        try:
            with open(self.state_file, 'r') as f:
                all_state = json.load(f)
        except FileNotFoundError:
            all_state = {}
        all_state[self.env] = self.state
        with open(self.state_file, 'w') as f:
            json.dump(all_state, f, indent=2)
    
    def get_vpc_id(self):
        if 'vpc_id' in self.state:
            return self.state['vpc_id']
        
        vpc_config = self.config.get('vpc', {})
        if 'vpc_id' in vpc_config:
            self.state['vpc_id'] = vpc_config['vpc_id']
            return self.state['vpc_id']
        
        vpc_cidr = vpc_config.get('cidr', '10.0.0.0/16')
        response = self.ec2.create_vpc(CidrBlock=vpc_cidr, TagSpecifications=[{
            'ResourceType': 'vpc',
            'Tags': [{'Key': 'Name', 'Value': f'{self.env}-vpc'}]
        }])
        vpc_id = response['Vpc']['VpcId']
        
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
        
        igw = self.ec2.create_internet_gateway(TagSpecifications=[{
            'ResourceType': 'internet-gateway',
            'Tags': [{'Key': 'Name', 'Value': f'{self.env}-igw'}]
        }])
        self.ec2.attach_internet_gateway(InternetGatewayId=igw['InternetGateway']['InternetGatewayId'], VpcId=vpc_id)
        self.state['igw_id'] = igw['InternetGateway']['InternetGatewayId']
        
        route_table = self.ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['RouteTables'][0]
        self.ec2.create_route(
            RouteTableId=route_table['RouteTableId'],
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw['InternetGateway']['InternetGatewayId']
        )
        
        self.state['vpc_id'] = vpc_id
        self.save_state()
        return vpc_id
    
    def get_subnet_ids(self):
        if 'subnet_ids' in self.state:
            return self.state['subnet_ids']
        
        vpc_id = self.get_vpc_id()
        subnet_ids = []
        subnets_config = self.config.get('vpc', {}).get('subnets', [])
        
        for subnet_config in subnets_config:
            response = self.ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=subnet_config['cidr'],
                AvailabilityZone=subnet_config['az'],
                TagSpecifications=[{
                    'ResourceType': 'subnet',
                    'Tags': [{'Key': 'Name', 'Value': f'{self.env}-subnet-{subnet_config["az"][-1]}'}]
                }]
            )
            subnet_ids.append(response['Subnet']['SubnetId'])
            self.ec2.modify_subnet_attribute(
                SubnetId=response['Subnet']['SubnetId'],
                MapPublicIpOnLaunch={'Value': True}
            )
        
        self.state['subnet_ids'] = subnet_ids
        self.save_state()
        return subnet_ids
    
    def create_security_groups(self):
        if 'alb_sg_id' in self.state and 'ec2_sg_id' in self.state:
            return self.state['alb_sg_id'], self.state['ec2_sg_id']
        
        vpc_id = self.get_vpc_id()
        
        alb_sg = self.ec2.create_security_group(
            GroupName=f'{self.env}-alb-sg',
            Description='Security group for ALB',
            VpcId=vpc_id,
            TagSpecifications=[{
                'ResourceType': 'security-group',
                'Tags': [{'Key': 'Name', 'Value': f'{self.env}-alb-sg'}]
            }]
        )
        alb_sg_id = alb_sg['GroupId']
        
        self.ec2.authorize_security_group_ingress(
            GroupId=alb_sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 80,
                'ToPort': 80,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            }]
        )
        
        ec2_sg = self.ec2.create_security_group(
            GroupName=f'{self.env}-ec2-sg',
            Description='Security group for EC2 instances',
            VpcId=vpc_id,
            TagSpecifications=[{
                'ResourceType': 'security-group',
                'Tags': [{'Key': 'Name', 'Value': f'{self.env}-ec2-sg'}]
            }]
        )
        ec2_sg_id = ec2_sg['GroupId']
        
        self.ec2.authorize_security_group_ingress(
            GroupId=ec2_sg_id,
            IpPermissions=[{
                'IpProtocol': 'tcp',
                'FromPort': 80,
                'ToPort': 80,
                'UserIdGroupPairs': [{'GroupId': alb_sg_id}]
            }]
        )
        
        self.state['alb_sg_id'] = alb_sg_id
        self.state['ec2_sg_id'] = ec2_sg_id
        self.save_state()
        return alb_sg_id, ec2_sg_id
    
    
    def create_launch_template(self):
        if 'launch_template_id' in self.state:
            return self.state['launch_template_id']
        
        existing_template_id = self.config.get('launch_template_id')
        
        if existing_template_id:
            with open('app.py', 'r') as f:
                app_py_content = f.read()
            
            app_py_b64 = self.base64_encode(app_py_content)
            
            with open('userdata.sh', 'r') as f:
                user_data_template = f.read()
            
            user_data = user_data_template.replace('__APP_PY_B64__', app_py_b64)
            
            _, ec2_sg_id = self.create_security_groups()
            
            try:
                existing_template = self.ec2.describe_launch_template_versions(
                    LaunchTemplateId=existing_template_id,
                    Versions=['$Latest']
                )
                base_data = existing_template['LaunchTemplateVersions'][0]['LaunchTemplateData']
            except Exception as e:
                raise Exception(f"Could not fetch existing template: {e}")
            
            if 'ImageId' not in base_data:
                raise Exception("Existing launch template does not have ImageId")
            
            launch_template_data = {
                'ImageId': base_data['ImageId'],
                'InstanceType': self.config['instance_type'],
                'SecurityGroupIds': [ec2_sg_id],
                'UserData': self.base64_encode(user_data),
            }
            
            if 'IamInstanceProfile' in base_data:
                launch_template_data['IamInstanceProfile'] = base_data['IamInstanceProfile']
            
            if 'KeyName' in base_data:
                launch_template_data['KeyName'] = base_data['KeyName']
            
            launch_template_data['TagSpecifications'] = [{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': f'{self.env}-instance'}]
            }]
            
            response = self.ec2.create_launch_template_version(
                LaunchTemplateId=existing_template_id,
                LaunchTemplateData=launch_template_data,
                SourceVersion='$Latest'
            )
            
            template_id = existing_template_id
            template_version = response['LaunchTemplateVersion']['VersionNumber']
            self.state['launch_template_id'] = template_id
            self.state['launch_template_version'] = template_version
            self.save_state()
            return template_id
        
        with open('app.py', 'r') as f:
            app_py_content = f.read()
        
        app_py_b64 = self.base64_encode(app_py_content)
        
        with open('userdata.sh', 'r') as f:
            user_data_template = f.read()
        
        user_data = user_data_template.replace('__APP_PY_B64__', app_py_b64)
        
        subnet_ids = self.get_subnet_ids()
        _, ec2_sg_id = self.create_security_groups()
        
        launch_template_data = {
            'ImageId': self.get_amazon_linux_ami(),
            'InstanceType': self.config['instance_type'],
            'SecurityGroupIds': [ec2_sg_id],
            'UserData': self.base64_encode(user_data),
            'TagSpecifications': [{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': f'{self.env}-instance'}]
            }]
        }
        
        response = self.ec2.create_launch_template(
            LaunchTemplateName=f'{self.env}-lt',
            LaunchTemplateData=launch_template_data,
            TagSpecifications=[{
                'ResourceType': 'launch-template',
                'Tags': [{'Key': 'Name', 'Value': f'{self.env}-lt'}]
            }]
        )
        
        template_id = response['LaunchTemplate']['LaunchTemplateId']
        self.state['launch_template_id'] = template_id
        self.save_state()
        return template_id
    
    def get_amazon_linux_ami(self):
        response = self.ec2.describe_images(
            Owners=['amazon'],
            Filters=[
                {'Name': 'name', 'Values': ['amzn2-ami-hvm-*-x86_64-gp2']},
                {'Name': 'state', 'Values': ['available']}
            ]
        )
        images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
        return images[0]['ImageId']
    
    def base64_encode(self, text):
        import base64
        import gzip
        compressed = gzip.compress(text.encode())
        return base64.b64encode(compressed).decode()
    
    def create_target_group(self):
        if 'target_group_arn' in self.state:
            return self.state['target_group_arn']
        
        vpc_id = self.get_vpc_id()
        subnet_ids = self.get_subnet_ids()
        
        response = self.elbv2.create_target_group(
            Name=f'{self.env}-tg',
            Protocol='HTTP',
            Port=80,
            VpcId=vpc_id,
            HealthCheckPath='/health',
            HealthCheckProtocol='HTTP',
            HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=5,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=2,
            TargetType='instance',
            Tags=[{'Key': 'Name', 'Value': f'{self.env}-tg'}]
        )
        
        tg_arn = response['TargetGroups'][0]['TargetGroupArn']
        self.state['target_group_arn'] = tg_arn
        self.save_state()
        return tg_arn
    
    def create_alb(self):
        if 'alb_arn' in self.state:
            return self.state['alb_arn']
        
        subnet_ids = self.get_subnet_ids()
        alb_sg_id, _ = self.create_security_groups()
        
        response = self.elbv2.create_load_balancer(
            Name=f'{self.env}-alb',
            Subnets=subnet_ids,
            SecurityGroups=[alb_sg_id],
            Scheme='internet-facing',
            Type='application',
            Tags=[{'Key': 'Name', 'Value': f'{self.env}-alb'}]
        )
        
        alb_arn = response['LoadBalancers'][0]['LoadBalancerArn']
        alb_dns = response['LoadBalancers'][0]['DNSName']
        
        time.sleep(10)
        
        tg_arn = self.create_target_group()
        
        self.elbv2.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol='HTTP',
            Port=80,
            DefaultActions=[{
                'Type': 'forward',
                'TargetGroupArn': tg_arn
            }]
        )
        
        self.state['alb_arn'] = alb_arn
        self.state['alb_dns'] = alb_dns
        self.save_state()
        return alb_arn, alb_dns
    
    def create_asg(self):
        if 'asg_name' in self.state:
            return self.state['asg_name']
        
        subnet_ids = self.get_subnet_ids()
        launch_template_id = self.create_launch_template()
        tg_arn = self.create_target_group()
        
        asg_name = f'{self.env}-asg'
        
        version = '$Latest'
        if 'launch_template_version' in self.state:
            version = str(self.state['launch_template_version'])
        
        self.autoscaling.create_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            LaunchTemplate={'LaunchTemplateId': launch_template_id, 'Version': version},
            MinSize=self.config['min_capacity'],
            MaxSize=self.config['max_capacity'],
            DesiredCapacity=self.config['desired_capacity'],
            VPCZoneIdentifier=','.join(subnet_ids),
            TargetGroupARNs=[tg_arn],
            HealthCheckType='ELB',
            HealthCheckGracePeriod=300,
            Tags=[{
                'Key': 'Name',
                'Value': f'{self.env}-asg-instance',
                'PropagateAtLaunch': True
            }]
        )
        
        scaling_config = self.config.get('scaling_policy', {})
        scale_out_threshold = scaling_config.get('scale_out_threshold', 50)
        scale_in_threshold = scaling_config.get('scale_in_threshold', 20)
        
        self.autoscaling.put_scaling_policy(
            AutoScalingGroupName=asg_name,
            PolicyName=f'{self.env}-scale-out',
            PolicyType='TargetTrackingScaling',
            TargetTrackingConfiguration={
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ASGAverageCPUUtilization'
                },
                'TargetValue': float(scale_out_threshold)
            }
        )
        
        self.state['asg_name'] = asg_name
        self.save_state()
        return asg_name
    
    def deploy(self):
        print(f"Deploying infrastructure for environment: {self.env}")
        print("Creating VPC and subnets...")
        self.get_vpc_id()
        self.get_subnet_ids()
        
        print("Creating security groups...")
        self.create_security_groups()
        
        print("Creating launch template...")
        self.create_launch_template()
        
        print("Creating target group...")
        self.create_target_group()
        
        print("Creating ALB...")
        alb_arn, alb_dns = self.create_alb()
        
        print("Creating Auto Scaling Group...")
        self.create_asg()
        
        print(f"\nDeployment complete!")
        print(f"ALB DNS: {alb_dns}")
        print(f"Waiting for instances to become healthy...")
        time.sleep(60)
        self.status()
    
    def status(self):
        if 'alb_dns' not in self.state:
            print(f"No deployment found for environment: {self.env}")
            return
        
        print(f"\n=== Infrastructure Status for {self.env} ===")
        print(f"ALB DNS: {self.state.get('alb_dns', 'N/A')}")
        
        if 'target_group_arn' in self.state:
            tg_arn = self.state['target_group_arn']
            try:
                tg_info = self.elbv2.describe_target_health(TargetGroupArn=tg_arn)
                healthy = sum(1 for t in tg_info['TargetHealthDescriptions'] if t['TargetHealth']['State'] == 'healthy')
                total = len(tg_info['TargetHealthDescriptions'])
                print(f"Target Group Health: {healthy}/{total} healthy")
                for target in tg_info['TargetHealthDescriptions']:
                    state = target['TargetHealth']['State']
                    print(f"  - {target['Target']['Id']}: {state}")
            except Exception as e:
                print(f"Target Group: Error - {e}")
        
        if 'asg_name' in self.state:
            try:
                asg_info = self.autoscaling.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[self.state['asg_name']]
                )
                if asg_info['AutoScalingGroups']:
                    asg = asg_info['AutoScalingGroups'][0]
                    print(f"ASG Size: Min={asg['MinSize']}, Desired={asg['DesiredCapacity']}, Max={asg['MaxSize']}")
                    print(f"ASG Instances: {len(asg['Instances'])}")
            except Exception as e:
                print(f"ASG: Error - {e}")
    
    def wait_for_instances_terminated(self, max_wait=300):
        if 'vpc_id' not in self.state:
            return
        
        vpc_id = self.state['vpc_id']
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                instances = self.ec2.describe_instances(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]},
                        {'Name': 'instance-state-name', 'Values': ['running', 'stopping', 'pending']}
                    ]
                )
                running = sum(1 for r in instances['Reservations'] for i in r['Instances'])
                if running == 0:
                    return
                print(f"  Waiting for {running} instance(s) to terminate...")
                time.sleep(10)
            except Exception:
                pass
        
        print("  Warning: Some instances may still be terminating")
    
    def cleanup_network_interfaces(self):
        if 'vpc_id' not in self.state:
            return
        
        vpc_id = self.state['vpc_id']
        try:
            enis = self.ec2.describe_network_interfaces(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for eni in enis['NetworkInterfaces']:
                if eni['Status'] != 'available':
                    continue
                try:
                    if eni.get('Attachment') and eni['Attachment'].get('AttachmentId'):
                        self.ec2.detach_network_interface(
                            AttachmentId=eni['Attachment']['AttachmentId'],
                            Force=True
                        )
                        time.sleep(2)
                    self.ec2.delete_network_interface(NetworkInterfaceId=eni['NetworkInterfaceId'])
                except Exception as e:
                    pass
        except Exception:
            pass
    
    def cleanup_route_tables(self):
        if 'vpc_id' not in self.state:
            return
        
        vpc_id = self.state['vpc_id']
        try:
            route_tables = self.ec2.describe_route_tables(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for rt in route_tables['RouteTables']:
                if rt.get('Associations'):
                    for assoc in rt['Associations']:
                        if not assoc.get('Main'):
                            try:
                                self.ec2.disassociate_route_table(
                                    AssociationId=assoc['RouteTableAssociationId']
                                )
                            except Exception:
                                pass
        except Exception:
            pass
    
    def destroy(self):
        print(f"Destroying infrastructure for environment: {self.env}")
        
        if 'asg_name' in self.state:
            print("Deleting Auto Scaling Group...")
            try:
                self.autoscaling.delete_auto_scaling_group(
                    AutoScalingGroupName=self.state['asg_name'],
                    ForceDelete=True
                )
            except Exception as e:
                print(f"Error deleting ASG: {e}")
        
        if 'launch_template_id' in self.state:
            existing_template_id = self.config.get('launch_template_id')
            if existing_template_id and self.state['launch_template_id'] == existing_template_id:
                print("Skipping Launch Template deletion (using existing template)...")
            else:
                print("Deleting Launch Template...")
                try:
                    self.ec2.delete_launch_template(LaunchTemplateId=self.state['launch_template_id'])
                except Exception as e:
                    print(f"Error deleting Launch Template: {e}")
        
        if 'alb_arn' in self.state:
            print("Deleting ALB...")
            try:
                listeners = self.elbv2.describe_listeners(LoadBalancerArn=self.state['alb_arn'])
                for listener in listeners['Listeners']:
                    try:
                        self.elbv2.delete_listener(ListenerArn=listener['ListenerArn'])
                    except Exception:
                        pass
                self.elbv2.delete_load_balancer(LoadBalancerArn=self.state['alb_arn'])
            except Exception as e:
                print(f"Error deleting ALB: {e}")
        
        if 'target_group_arn' in self.state:
            print("Deleting Target Group...")
            try:
                self.elbv2.delete_target_group(TargetGroupArn=self.state['target_group_arn'])
            except Exception as e:
                print(f"Error deleting Target Group: {e}")
        
        print("Cleaning up network interfaces...")
        self.cleanup_network_interfaces()
        
        print("Cleaning up route tables...")
        self.cleanup_route_tables()
        
        if 'ec2_sg_id' in self.state:
            print("Deleting EC2 Security Group...")
            try:
                self.ec2.delete_security_group(GroupId=self.state['ec2_sg_id'])
            except Exception as e:
                print(f"  Warning: Could not delete EC2 Security Group: {e}")
        
        if 'alb_sg_id' in self.state:
            print("Deleting ALB Security Group...")
            try:
                self.ec2.delete_security_group(GroupId=self.state['alb_sg_id'])
            except Exception as e:
                print(f"  Warning: Could not delete ALB Security Group: {e}")
        
        if 'subnet_ids' in self.state:
            print("Deleting Subnets...")
            for subnet_id in self.state['subnet_ids']:
                for attempt in range(3):
                    try:
                        self.ec2.delete_subnet(SubnetId=subnet_id)
                        break
                    except ClientError as e:
                        if 'DependencyViolation' in str(e) and attempt < 2:
                            time.sleep(10)
                            continue
                        print(f"  Error deleting subnet {subnet_id}: {e}")
                        break
        
        if 'igw_id' in self.state and 'vpc_id' in self.state:
            print("Detaching and deleting Internet Gateway...")
            for attempt in range(3):
                try:
                    self.ec2.detach_internet_gateway(
                        InternetGatewayId=self.state['igw_id'],
                        VpcId=self.state['vpc_id']
                    )
                    time.sleep(2)
                    self.ec2.delete_internet_gateway(InternetGatewayId=self.state['igw_id'])
                    break
                except ClientError as e:
                    if 'DependencyViolation' in str(e) and attempt < 2:
                        time.sleep(10)
                        continue
                    print(f"  Error: {e}")
                    break
        
        if 'vpc_id' in self.state:
            print("Deleting VPC...")
            for attempt in range(3):
                try:
                    self.ec2.delete_vpc(VpcId=self.state['vpc_id'])
                    break
                except ClientError as e:
                    if 'DependencyViolation' in str(e) and attempt < 2:
                        print("  Waiting for dependencies to clear...")
                        time.sleep(15)
                        continue
                    print(f"  Error: {e}")
                    break
        
        self.state = {}
        self.save_state()
        print("\nDestroy complete!")

def main():
    parser = argparse.ArgumentParser(description='Deploy and manage AWS infrastructure')
    parser.add_argument('command', choices=['deploy', 'status', 'destroy'], help='Command to execute')
    parser.add_argument('--env', default='dev', help='Environment name (default: dev)')
    parser.add_argument('--config', default='config.yaml', help='Config file path (default: config.yaml)')
    
    args = parser.parse_args()
    
    deployer = InfrastructureDeployer(args.config, args.env)
    
    if args.command == 'deploy':
        deployer.deploy()
    elif args.command == 'status':
        deployer.status()
    elif args.command == 'destroy':
        deployer.destroy()

if __name__ == '__main__':
    main()
