import boto3
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')  # Required for temporary credentials
AWS_REGION = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')

# Check if using temporary credentials (starts with ASIA)
is_temporary = AWS_ACCESS_KEY_ID and AWS_ACCESS_KEY_ID.startswith('ASIA')

# Validate credentials are loaded
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    print("ERROR: AWS credentials not found!")
    print("Please create a .env file with:")
    print("  AWS_ACCESS_KEY_ID=your_access_key")
    print("  AWS_SECRET_ACCESS_KEY=your_secret_key")
    if is_temporary:
        print("  AWS_SESSION_TOKEN=your_session_token  # Required for temporary credentials")
    print("\nOr set them as environment variables.")
    sys.exit(1)

# Temporary credentials require a session token
if is_temporary and not AWS_SESSION_TOKEN:
    print("ERROR: Temporary credentials detected (starts with ASIA) but AWS_SESSION_TOKEN is missing!")
    print("\nTemporary credentials require a session token. Add to .env:")
    print("  AWS_SESSION_TOKEN=your_session_token")
    print("\nOr use permanent IAM credentials (starts with AKIA) instead.")
    sys.exit(1)

# Build client kwargs
client_kwargs = {
    'aws_access_key_id': AWS_ACCESS_KEY_ID,
    'aws_secret_access_key': AWS_SECRET_ACCESS_KEY,
    'region_name': AWS_REGION
}
if AWS_SESSION_TOKEN:
    client_kwargs['aws_session_token'] = AWS_SESSION_TOKEN

# First, verify credentials work with STS
try:
    sts_client = boto3.client('sts', **client_kwargs)
    identity = sts_client.get_caller_identity()
    print(f"✓ Authenticated as: {identity.get('Arn', 'Unknown')}")
    print(f"✓ Account ID: {identity.get('Account', 'Unknown')}")
    if is_temporary:
        print(f"✓ Using temporary credentials (expires in ~1 hour)")
except Exception as e:
    print(f"✗ Authentication failed: {e}")
    print("\nPossible issues:")
    if is_temporary:
        print("  1. Temporary credentials have EXPIRED (they last ~1 hour)")
        print("  2. AWS_SESSION_TOKEN is missing or invalid")
        print("  3. Get fresh credentials from your AWS SSO/CLI")
    else:
        print("  1. Credentials are invalid or expired")
    print("  2. Credentials don't have necessary permissions")
    print("  3. Network connectivity issues")
    print("\nTo fix:")
    if is_temporary:
        print("  - Run: aws sso login  (if using AWS SSO)")
        print("  - Or: aws sts get-session-token  (if using MFA)")
        print("  - Then update your .env file with fresh credentials")
    else:
        print("  - Check your .env file has correct credentials")
        print("  - Or use: aws configure")
    sys.exit(1)

# Now try EC2
print(f"\nFetching VPCs from region: {AWS_REGION}")
print("=" * 50)

try:
    ec2 = boto3.client('ec2', **client_kwargs)
    
    vpc_list = ec2.describe_vpcs()
    
    if not vpc_list['Vpcs']:
        print("No VPCs found in this region.")
    else:
        print(f"Found {len(vpc_list['Vpcs'])} VPC(s):\n")
        for vpc in vpc_list['Vpcs']:
            vpc_id = vpc['VpcId']
            cidr = vpc.get('CidrBlock', 'N/A')
            tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
            name = tags.get('Name', 'unnamed')
            print(f"VPC ID: {vpc_id}")
            print(f"  Name: {name}")
            print(f"  CIDR: {cidr}")
            print(f"  State: {vpc.get('State', 'N/A')}")
            print()
except Exception as e:
    print(f"✗ Error fetching VPCs: {e}")
    print("\nThis might be a permissions issue.")
    print("Ensure your IAM user/role has 'ec2:DescribeVpcs' permission.")
    sys.exit(1)
