"""Deploy the bounded, authenticated AWS research application.

Run from the repository with .venv/Scripts/python.exe -m scripts.deploy_application.
Uses the ignored project login cache when present. Never upgrades a Free plan.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from infra.build_application import template

ROOT=Path(__file__).resolve().parents[1]
REGION='ap-southeast-2'
STACK='rewindguard-x-app'


def session():
    cache=ROOT/'.local-authority'/'login-cache'
    if cache.is_dir(): os.environ['AWS_LOGIN_CACHE_DIRECTORY']=str(cache)
    return boto3.Session(profile_name='rewindguard',region_name=REGION)


def sdk(sess,name,region=REGION):
    return sess.client(name,region_name=region,config=Config(connect_timeout=5,read_timeout=30,
                       retries={'mode':'standard','total_max_attempts':2}))


def free_guard(sess):
    result=sdk(sess,'freetier','us-east-1').get_account_plan_state()
    credit=result.get('accountPlanRemainingCredits',{})
    if result.get('accountPlanType')!='FREE' or result.get('accountPlanStatus')!='ACTIVE' or credit.get('unit')!='USD' or credit.get('amount',0)<=0:
        raise RuntimeError('Deployment held: FREE/ACTIVE project and positive remaining credits are required (no fixed reserve)')
    return result


def bundle():
    deps=ROOT/'.lambda-deps'
    wheels=list(deps.glob('psycopg_binary-*.dist-info/WHEEL'))
    if not wheels or 'cp312' not in wheels[0].read_text() or 'manylinux' not in wheels[0].read_text():
        raise RuntimeError('Linux CPython 3.12 psycopg binary dependencies must be prepared first')
    build=ROOT/'build'
    build.mkdir(exist_ok=True)
    path=build/'cloud-application.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for folder in (ROOT/'backend',ROOT/'contracts'):
            for item in folder.rglob('*'):
                if item.is_file() and '__pycache__' not in item.parts and item.suffix in {'.py','.sql','.pem'}:
                    archive.write(item,item.relative_to(ROOT).as_posix())
        for name in ('cloud.html','cloud.js','cloud.css','style.css'):
            archive.write(ROOT/'frontend'/name,'frontend/'+name)
        for item in deps.rglob('*'):
            if item.is_file() and '__pycache__' not in item.parts:
                archive.write(item,item.relative_to(deps).as_posix())
    return path


def stack_outputs(cf,name):
    info=cf.describe_stacks(StackName=name)['Stacks'][0]
    if not any(t['Key']=='Project' and t['Value']=='rewindguard-x' for t in info.get('Tags',[])):
        raise RuntimeError('Stack does not belong to the tagged project')
    return info,{v['OutputKey']:v['OutputValue'] for v in info.get('Outputs',[])}


def wait_stack(cf,operation):
    print('Waiting for private application stack '+operation+'...',flush=True)
    cf.get_waiter('stack_'+operation+'_complete').wait(StackName=STACK,WaiterConfig={'Delay':15,'MaxAttempts':160})


def preserve_lifecycle_parameters(params,existing):
    previous={x['ParameterKey']:x.get('ParameterValue') for x in existing.get('Parameters',[])}
    # Code updates must never re-enable paid bootstrap networking or recreate
    # the original database that the owner explicitly retired.
    for key in ('EnableBootstrap','RetainOriginalDatabase'):
        if key in previous: params[key]=previous[key]
    return params


def deploy(sess):
    free_guard(sess)
    cf=sdk(sess,'cloudformation')
    _,core=stack_outputs(cf,'rewindguard-x-core')
    payload=bundle()
    digest=hashlib.sha256(payload.read_bytes()).hexdigest()[:24]
    s3=sdk(sess,'s3')
    key='deployments/cloud-'+digest+'.zip'
    uploaded=s3.put_object(Bucket=core['DocumentBucketName'],Key=key,Body=payload.read_bytes(),ContentType='application/zip')
    body=json.dumps(template(),separators=(',',':')).encode()
    template_key='deployments/application-'+hashlib.sha256(body).hexdigest()[:24]+'.json'
    template_upload=s3.put_object(Bucket=core['DocumentBucketName'],Key=template_key,Body=body,ContentType='application/json')
    url='https://'+core['DocumentBucketName']+'.s3.'+REGION+'.amazonaws.com/'+template_key+'?versionId='+quote(template_upload['VersionId'],safe='')
    rds=sdk(sess,'rds')
    options=[]
    for page in rds.get_paginator('describe_orderable_db_instance_options').paginate(Engine='postgres',DBInstanceClass='db.t4g.micro',Vpc=True):
        options.extend(page['OrderableDBInstanceOptions'])
    versions={x['EngineVersion'] for x in options if x['EngineVersion'].startswith('17.') and '-rds.' not in x['EngineVersion']}
    if not versions: raise RuntimeError('PostgreSQL 17 micro instance is unavailable in the project Region')
    version=max(versions,key=lambda value:tuple(int(x) for x in value.split('.')))
    params={'AuthorityTableName':core['AuthorityTableName'],'DocumentBucketName':core['DocumentBucketName'],
            'CodeKey':key,'CodeVersion':uploaded['VersionId'],'DbEngineVersion':version,
            'DemoExpiresAt':str(int(time.time())+7200),'EnableBootstrap':'true','RetainOriginalDatabase':'true'}
    try:
        existing,_=stack_outputs(cf,STACK)
        kind='CREATE' if existing['StackStatus']=='REVIEW_IN_PROGRESS' else 'UPDATE'
        if kind=='UPDATE': preserve_lifecycle_parameters(params,existing)
        if existing['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE','REVIEW_IN_PROGRESS'}:
            raise RuntimeError('Existing application stack needs inspection: '+existing['StackStatus'])
    except ClientError as error:
        if 'does not exist' not in error.response['Error']['Message']: raise
        kind='CREATE'
    change='app-'+str(int(time.time()))
    cf.create_change_set(StackName=STACK,ChangeSetName=change,ChangeSetType=kind,TemplateURL=url,
        Parameters=[{'ParameterKey':k,'ParameterValue':v} for k,v in params.items()],
        Capabilities=['CAPABILITY_IAM'],Tags=[{'Key':'Project','Value':'rewindguard-x'},{'Key':'Purpose','Value':'synthetic-course-demo'}])
    cf.get_waiter('change_set_create_complete').wait(StackName=STACK,ChangeSetName=change,WaiterConfig={'Delay':5,'MaxAttempts':60})
    changes=cf.describe_change_set(StackName=STACK,ChangeSetName=change)['Changes']
    counts={}
    for change_item in changes:
        item=change_item['ResourceChange']
        counts[item['ResourceType']]=counts.get(item['ResourceType'],0)+1
        if item['Action']=='Remove' and kind=='CREATE': raise RuntimeError('Unexpected destructive change')
    print(json.dumps({'action':'deploy_private_application','change_type':kind,'resources':counts,'region':REGION},indent=2),flush=True)
    free_guard(sess)
    cf.execute_change_set(StackName=STACK,ChangeSetName=change)
    wait_stack(cf,'create' if kind=='CREATE' else 'update')
    _,outputs=stack_outputs(cf,STACK)
    bootstrap=outputs.get('BootstrapFunctionName')
    if bootstrap and bootstrap!='disabled':
        print('Initializing schema and IAM database users through the private bootstrap worker...',flush=True)
        result=sdk(sess,'lambda').invoke(FunctionName=bootstrap,Payload=b'{"action":"bootstrap"}')
        reply=json.loads(result['Payload'].read())
        if result.get('FunctionError') or not reply.get('ok'):
            raise RuntimeError('Database bootstrap held. Inspect the bootstrap Lambda log; credentials are not printed.')
        info,_=stack_outputs(cf,STACK)
        updates=[{'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in info['Parameters'] if p['ParameterKey']!='EnableBootstrap']
        updates.append({'ParameterKey':'EnableBootstrap','ParameterValue':'false'})
        cf.update_stack(StackName=STACK,UsePreviousTemplate=True,Parameters=updates,Capabilities=['CAPABILITY_IAM'])
        wait_stack(cf,'update')
        _,outputs=stack_outputs(cf,STACK)
    dest=ROOT/'evidence'/'private'
    dest.mkdir(parents=True,exist_ok=True)
    (dest/'application-outputs.json').write_text(json.dumps(outputs,indent=2),encoding='utf-8')
    print(json.dumps({'status':'APPLICATION_DEPLOYED','api_url':outputs['ApiUrl'],'ui_url':outputs['ApiUrl']+'/ui',
                     'database':'private RDS PostgreSQL; IAM authentication','bootstrap_access':'removed',
                     'standby_after':'two-hour safety fallback; stop immediately after evaluation'},indent=2),flush=True)
    return outputs


if __name__=='__main__':
    deploy(session())
