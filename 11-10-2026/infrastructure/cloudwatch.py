import boto3
from botocore.exceptions import ClientError
from .state import add_resource

def create_scaling_policies(cloudwatch_client, autoscaling_client, asg_name: str,
                            scale_out_threshold: int, scale_in_threshold: int,
                            environment: str, deployment_id: str):
    try:
        scale_out_policy_name = f"webapp-scale-out-{environment}"
        scale_in_policy_name = f"webapp-scale-in-{environment}"
        
        scale_out_policy = autoscaling_client.put_scaling_policy(
            AutoScalingGroupName=asg_name,
            PolicyName=scale_out_policy_name,
            PolicyType='TargetTrackingScaling',
            TargetTrackingConfiguration={
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ASGAverageCPUUtilization'
                },
                'TargetValue': float(scale_out_threshold)
            }
        )
        
        scale_out_policy_arn = scale_out_policy['PolicyARN']
        add_resource(deployment_id, 'scaling_policy', scale_out_policy_arn, scale_out_policy_name)
        
        scale_out_alarm_name = f"webapp-cpu-high-{environment}"
        cloudwatch_client.put_metric_alarm(
            AlarmName=scale_out_alarm_name,
            ComparisonOperator='GreaterThanThreshold',
            EvaluationPeriods=2,
            MetricName='CPUUtilization',
            Namespace='AWS/EC2',
            Period=300,
            Statistic='Average',
            Threshold=float(scale_out_threshold),
            AlarmDescription='Alarm when CPU exceeds threshold',
            Dimensions=[
                {
                    'Name': 'AutoScalingGroupName',
                    'Value': asg_name
                }
            ],
            AlarmActions=[scale_out_policy_arn]
        )
        
        add_resource(deployment_id, 'cloudwatch_alarm', scale_out_alarm_name, scale_out_alarm_name)
        
        scale_in_alarm_name = f"webapp-cpu-low-{environment}"
        cloudwatch_client.put_metric_alarm(
            AlarmName=scale_in_alarm_name,
            ComparisonOperator='LessThanThreshold',
            EvaluationPeriods=2,
            MetricName='CPUUtilization',
            Namespace='AWS/EC2',
            Period=300,
            Statistic='Average',
            Threshold=float(scale_in_threshold),
            AlarmDescription='Alarm when CPU below threshold',
            Dimensions=[
                {
                    'Name': 'AutoScalingGroupName',
                    'Value': asg_name
                }
            ]
        )
        
        add_resource(deployment_id, 'cloudwatch_alarm', scale_in_alarm_name, scale_in_alarm_name)
        
    except ClientError as e:
        raise Exception(f"Failed to create CloudWatch alarms: {e}")
