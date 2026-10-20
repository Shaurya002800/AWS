"""Shared cloud serialization and bounded AWS clients."""
import json
import os
import re
from dataclasses import asdict, is_dataclass


def client(service):
    import boto3
    from botocore.config import Config
    return boto3.client(service, region_name=os.environ.get('AWS_REGION', 'ap-southeast-2'),
                        config=Config(connect_timeout=5, read_timeout=25,
                                      retries={'mode':'standard','total_max_attempts':2}))


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}',value):
        raise ValueError('Invalid synthetic identifier')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',',':'),ensure_ascii=False).encode()


def invoke(function, event):
    response=client('lambda').invoke(FunctionName=function,InvocationType='RequestResponse',Payload=canonical(event))
    body=json.loads(response['Payload'].read())
    if response.get('FunctionError') or not body.get('ok'):
        raise RuntimeError('Database task failed: '+str(body.get('code','LAMBDA_FAILURE')))
    return body.get('result')


def success(value):
    return {'ok':True,'result':asdict(value) if is_dataclass(value) else value}


def authority():
    from backend.eraselock.dynamodb_authority import DynamoDBAuthority
    return DynamoDBAuthority(client('dynamodb'),os.environ['AUTHORITY_TABLE'])
