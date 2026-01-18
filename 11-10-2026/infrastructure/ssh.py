import boto3
import os
from botocore.exceptions import ClientError
from .state import add_resource, get_resource_by_type

def create_or_get_key_pair(ec2_client, environment: str, deployment_id: str):
    key_name = f"webapp-key-{environment}"
    
    try:
        existing = ec2_client.describe_key_pairs(KeyNames=[key_name])
        if existing['KeyPairs']:
            key_pair = existing['KeyPairs'][0]
            add_resource(deployment_id, 'key_pair', key_pair['KeyName'], key_name)
            return key_pair['KeyName'], None
    except ClientError:
        pass
    
    try:
        response = ec2_client.create_key_pair(KeyName=key_name)
        private_key = response['KeyMaterial']
        
        key_file = f"webapp-key-{environment}.pem"
        with open(key_file, 'w') as f:
            f.write(private_key)
        os.chmod(key_file, 0o400)
        
        add_resource(deployment_id, 'key_pair', key_name, key_name)
        return key_name, key_file
    except ClientError as e:
        raise Exception(f"Failed to create key pair: {e}")

def get_ssh_key_file(environment: str):
    key_file = f"webapp-key-{environment}.pem"
    if os.path.exists(key_file):
        return key_file
    return None
