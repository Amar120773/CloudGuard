import boto3
from moto import mock_aws
import os

# Ensure we always use mocked AWS credentials in development
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

def get_boto3_client(service_name: str):
    """
    Returns a mocked boto3 client.
    In a real-world scenario, this would check if we're in prod and return a real client.
    """
    return boto3.client(service_name, region_name="us-east-1")
