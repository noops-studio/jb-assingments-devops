import boto3
import json
from botocore.exceptions import ClientError
from .state import add_resource

def create_iam_role(iam_client, s3_bucket_name: str, environment: str, deployment_id: str) -> str:
    role_name = f"webapp-ec2-role-{environment}"
    policy_name = f"webapp-s3-policy-{environment}"
    
    try:
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"IAM role for EC2 instances to access S3 bucket {s3_bucket_name}"
        )
        role_arn = role_response['Role']['Arn']
        
        s3_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:ListBucket"
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{s3_bucket_name}",
                        f"arn:aws:s3:::{s3_bucket_name}/*"
                    ]
                }
            ]
        }
        
        policy_response = iam_client.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(s3_policy),
            Description=f"Policy for S3 read access to {s3_bucket_name}"
        )
        policy_arn = policy_response['Policy']['Arn']
        
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn
        )
        
        instance_profile_name = f"webapp-ec2-profile-{environment}"
        try:
            profile_response = iam_client.create_instance_profile(
                InstanceProfileName=instance_profile_name
            )
            import time
            time.sleep(2)
        except ClientError as e:
            if e.response['Error']['Code'] != 'EntityAlreadyExists':
                raise
        
        try:
            iam_client.add_role_to_instance_profile(
                InstanceProfileName=instance_profile_name,
                RoleName=role_name
            )
        except ClientError as e:
            if e.response['Error']['Code'] != 'LimitExceeded':
                raise
        
        add_resource(deployment_id, 'iam_role', role_arn, role_name)
        add_resource(deployment_id, 'iam_policy', policy_arn, policy_name)
        add_resource(deployment_id, 'iam_instance_profile', instance_profile_name, instance_profile_name)
        
        return instance_profile_name
    except ClientError as e:
        raise Exception(f"Failed to create IAM role: {e}")
