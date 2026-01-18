import boto3
import time
import os
import base64
from botocore.exceptions import ClientError
from .state import add_resource

def get_latest_amazon_linux_ami(ec2_client, region: str):
    try:
        response = ec2_client.describe_images(
            Owners=['amazon'],
            Filters=[
                {'Name': 'name', 'Values': ['amzn2-ami-hvm-*-x86_64-gp2']},
                {'Name': 'state', 'Values': ['available']}
            ]
        )
        
        images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
        if images:
            return images[0]['ImageId']
        
        raise Exception("No Amazon Linux 2 AMI found")
    except ClientError as e:
        raise Exception(f"Failed to get AMI: {e}")

def get_userdata_script():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    script_path = os.path.join(project_root, 'scripts', 'userdata.sh')
    with open(script_path, 'r') as f:
        script_content = f.read()
    return base64.b64encode(script_content.encode('utf-8')).decode('utf-8')

def create_launch_template(ec2_client, instance_type: str, security_group_id: str,
                           environment: str, deployment_id: str, region: str, key_name: str = None):
    try:
        template_name = f"webapp-lt-{environment}"
        
        ami_id = get_latest_amazon_linux_ami(ec2_client, region)
        userdata = get_userdata_script()
        
        launch_data = {
            'ImageId': ami_id,
            'InstanceType': instance_type,
            'UserData': userdata,
            'TagSpecifications': [
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': f"webapp-instance-{environment}"}
                    ]
                }
            ],
            'NetworkInterfaces': [
                {
                    'AssociatePublicIpAddress': True,
                    'DeviceIndex': 0,
                    'Groups': [security_group_id]
                }
            ]
        }
        
        if key_name:
            launch_data['KeyName'] = key_name
        
        response = ec2_client.create_launch_template(
            LaunchTemplateName=template_name,
            LaunchTemplateData=launch_data,
            TagSpecifications=[
                {
                    'ResourceType': 'launch-template',
                    'Tags': [
                        {'Key': 'Name', 'Value': template_name}
                    ]
                }
            ]
        )
        
        template_id = response['LaunchTemplate']['LaunchTemplateId']
        add_resource(deployment_id, 'launch_template', template_id, template_name)
        
        return template_id
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidLaunchTemplateName.AlreadyExistsException':
            existing = ec2_client.describe_launch_templates(
                LaunchTemplateNames=[template_name]
            )
            template_id = existing['LaunchTemplate'][0]['LaunchTemplateId']
            add_resource(deployment_id, 'launch_template', template_id, template_name)
            return template_id
        raise Exception(f"Failed to create launch template: {e}")

def create_asg(autoscaling_client, ec2_client, elbv2_client, target_group_arn: str, 
                subnets: list, ec2_sg_id: str, asg_config: dict, instance_config: dict,
                environment: str, deployment_id: str, region: str) -> str:
    try:
        asg_name = f"webapp-asg-{environment}"
        
        from .ssh import create_or_get_key_pair
        key_name, key_file = create_or_get_key_pair(ec2_client, environment, deployment_id)
        if key_file:
            print(f"SSH key pair created: {key_name}")
            print(f"Private key saved to: {key_file}")
        
        launch_template_id = asg_config.get('launch_template_id')
        
        if not launch_template_id or launch_template_id == 'null' or launch_template_id is None:
            print("Creating launch template...")
            launch_template_id = create_launch_template(
                ec2_client, instance_config['type'], ec2_sg_id,
                environment, deployment_id, region, key_name
            )
            print(f"Launch template created: {launch_template_id}")
        else:
            try:
                ec2_client.describe_launch_templates(LaunchTemplateIds=[launch_template_id])
                print(f"Using existing launch template: {launch_template_id}")
            except ClientError:
                print(f"Launch template {launch_template_id} not found, creating new one...")
                launch_template_id = create_launch_template(
                    ec2_client, instance_config['type'], ec2_sg_id,
                    environment, deployment_id, region, key_name
                )
                print(f"Launch template created: {launch_template_id}")
        
        response = autoscaling_client.create_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            LaunchTemplate={
                'LaunchTemplateId': launch_template_id,
                'Version': '$Latest'
            },
            MinSize=asg_config['min_size'],
            MaxSize=asg_config['max_size'],
            DesiredCapacity=asg_config['desired_capacity'],
            VPCZoneIdentifier=','.join(subnets),
            TargetGroupARNs=[target_group_arn],
            HealthCheckType='ELB',
            HealthCheckGracePeriod=300,
            Tags=[
                {
                    'Key': 'Name',
                    'Value': f"webapp-instance-{environment}",
                    'PropagateAtLaunch': True
                }
            ]
        )
        
        add_resource(deployment_id, 'asg', asg_name, asg_name)
        
        print("Waiting for instances to launch and become healthy...")
        print("This may take 2-3 minutes for instances to boot and install dependencies...")
        time.sleep(60)
        
        return asg_name
    except ClientError as e:
        raise Exception(f"Failed to create ASG: {e}")
