import boto3
from botocore.exceptions import ClientError
from .state import add_resource, get_resource_by_type, get_resources

def create_vpc(ec2_client, vpc_config: dict, environment: str, deployment_id: str) -> dict:
    try:
        existing_vpc = get_resource_by_type(deployment_id, 'vpc')
        if existing_vpc:
            try:
                vpc_info = ec2_client.describe_vpcs(VpcIds=[existing_vpc['resource_id']])
                if vpc_info['Vpcs']:
                    vpc_id = existing_vpc['resource_id']
                    
                    existing_subnets = [r for r in get_resources(deployment_id, 'subnet')]
                    existing_alb_sg = get_resource_by_type(deployment_id, 'security_group')
                    existing_ec2_sg = None
                    
                    for sg in get_resources(deployment_id, 'security_group'):
                        if sg.get('resource_name') == 'alb-sg':
                            existing_alb_sg = sg
                        elif sg.get('resource_name') == 'ec2-sg':
                            existing_ec2_sg = sg
                    
                    if existing_subnets and existing_alb_sg and existing_ec2_sg:
                        subnet_ids = [s['resource_id'] for s in existing_subnets]
                        return {
                            'vpc_id': vpc_id,
                            'subnets': subnet_ids,
                            'alb_sg_id': existing_alb_sg['resource_id'],
                            'ec2_sg_id': existing_ec2_sg['resource_id']
                        }
            except ClientError:
                pass
        
        vpc_response = ec2_client.create_vpc(
            CidrBlock=vpc_config['cidr'],
            TagSpecifications=[
                {
                    'ResourceType': 'vpc',
                    'Tags': [{'Key': 'Name', 'Value': f"webapp-vpc-{environment}"}]
                }
            ]
        )
        vpc_id = vpc_response['Vpc']['VpcId']
        
        ec2_client.modify_vpc_attribute(
            VpcId=vpc_id,
            EnableDnsHostnames={'Value': True}
        )
        ec2_client.modify_vpc_attribute(
            VpcId=vpc_id,
            EnableDnsSupport={'Value': True}
        )
        
        add_resource(deployment_id, 'vpc', vpc_id, f"webapp-vpc-{vpc_config.get('environment', 'dev')}")
        
        igw_response = ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        
        ec2_client.attach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id
        )
        
        add_resource(deployment_id, 'internet_gateway', igw_id)
        
        subnets = []
        for subnet_config in vpc_config.get('subnets', []):
            subnet_response = ec2_client.create_subnet(
                VpcId=vpc_id,
                CidrBlock=subnet_config['cidr'],
                AvailabilityZone=subnet_config['az'],
                TagSpecifications=[
                    {
                        'ResourceType': 'subnet',
                        'Tags': [{'Key': 'Name', 'Value': f"webapp-subnet-{subnet_config['az']}"}]
                    }
                ]
            )
            subnet_id = subnet_response['Subnet']['SubnetId']
            
            # Enable auto-assign public IP for instances in this subnet
            ec2_client.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={'Value': True}
            )
            
            subnets.append(subnet_id)
            add_resource(deployment_id, 'subnet', subnet_id, f"webapp-subnet-{subnet_config['az']}")
        
        route_table_response = ec2_client.create_route_table(VpcId=vpc_id)
        route_table_id = route_table_response['RouteTable']['RouteTableId']
        
        ec2_client.create_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        
        for subnet_id in subnets:
            ec2_client.associate_route_table(
                RouteTableId=route_table_id,
                SubnetId=subnet_id
            )
        
        add_resource(deployment_id, 'route_table', route_table_id)
        
        alb_sg_response = ec2_client.create_security_group(
            GroupName=f"webapp-alb-sg-{environment}",
            Description='Security group for ALB',
            VpcId=vpc_id
        )
        alb_sg_id = alb_sg_response['GroupId']
        
        ec2_client.authorize_security_group_ingress(
            GroupId=alb_sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 80,
                    'ToPort': 80,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }
            ]
        )
        
        add_resource(deployment_id, 'security_group', alb_sg_id, 'alb-sg')
        
        ec2_sg_response = ec2_client.create_security_group(
            GroupName=f"webapp-ec2-sg-{environment}",
            Description='Security group for EC2 instances',
            VpcId=vpc_id
        )
        ec2_sg_id = ec2_sg_response['GroupId']
        
        ec2_client.authorize_security_group_ingress(
            GroupId=ec2_sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 80,
                    'ToPort': 80,
                    'UserIdGroupPairs': [{'GroupId': alb_sg_id}]
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 22,
                    'ToPort': 22,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'SSH access for debugging'}]
                }
            ]
        )
        
        add_resource(deployment_id, 'security_group', ec2_sg_id, 'ec2-sg')
        
        return {
            'vpc_id': vpc_id,
            'subnets': subnets,
            'alb_sg_id': alb_sg_id,
            'ec2_sg_id': ec2_sg_id
        }
    except ClientError as e:
        raise Exception(f"Failed to create VPC: {e}")
