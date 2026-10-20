"""Run real authenticated cloud checks and create an evidence-based report.

Creates a temporary synthetic Cognito user without sending notifications, then
revokes/deletes it. No password or token is printed or saved.
"""
import base64
import copy
from datetime import datetime,timezone
import json
import secrets
import statistics
import time
from pathlib import Path
from uuid import uuid4
import httpx
from scripts.deploy_application import ROOT,REGION,STACK,session,sdk,free_guard,stack_outputs
from backend.cloud.signing import KmsEvidenceSigner


def call(base,token,path,payload=None):
    start=time.perf_counter()
    response=httpx.request('POST' if payload is not None else 'GET',base+path,
        headers={'Authorization':'Bearer '+token} if token else {},json=payload,timeout=35)
    return response,time.perf_counter()-start


def report_file(result):
    tests=result['checks']
    outcome=result.get('workflow_result',{})
    text=f'''# RewindGuard X — cloud evaluation

Measured at {result['measured_at']} in `{REGION}`. This is a synthetic research
prototype result, not a production security certification or legal assessment.

## Actual deployment

Private RDS PostgreSQL, isolated snapshot restoration, Lambda workers,
Step Functions recovery, API Gateway JWT authorization, Cognito authorization
code/PKCE client, asymmetric KMS evidence signatures, scoped service IAM roles,
CloudWatch logs/alarms, and bounded cleanup. The earlier S3/DynamoDB/SQS core
remains in use. No public database or NAT gateway is created.

## Observed checks

| Check | Observed pass |
| --- | --- |
'''
    text+=''.join(f'| {key} | {value} |\n' for key,value in tests.items())
    text+=f'''
## Recovery observation

The candidate's raw pre-reconciliation counts were `{outcome.get('raw_before')}`;
post-reconciliation counts were `{outcome.get('raw_after')}`. The guarded profile
before reconciliation was `{outcome.get('guarded_before')}`. Publication result:
`{outcome.get('release')}`. The raw SQL oracle is confined to the trusted recovery
worker and is not a public endpoint. This compares historical raw state against
the guarded application; it is not a matched public-exposure-rate experiment.

Measured cloud recovery duration: {result.get('workflow_seconds')} seconds.
Authenticated read median: {result.get('read_median_ms')} ms; p95:
{result.get('read_p95_ms')} ms across {result.get('read_samples')} samples.
Small-sample p95 is descriptive, not a throughput benchmark.

## Cost evidence and limits

The Free-plan API was checked before and after the run. Remaining credits are
kept privately with the raw evidence. Difference observed during the run:
{result.get('observed_credit_delta_usd')} USD. This is not a final bill: metering
and credits can be delayed. Database configuration, workflow duration, and sampled request latencies are
recorded in private evidence. No claim of an exact finalized charge is made.
The project must remain on the Free plan for the owner's zero-out-of-pocket
requirement. There is no fixed credit reserve. A two-hour idle safety fallback remains;
databases are stopped immediately after evaluation. Idle serving databases are stopped; expired unused candidates and their
synthetic snapshots are removed. Stopped RDS storage, secrets, the signing key,
and monitoring can continue using credits. Recheck usage before each demo.

## Boundaries

Only mediated reads, writes, queue consumers, known S3 versions and configured
database/index tables are covered. Privileged administrators, unknown copies,
and training-weight unlearning are outside the guarantee. Ledger/journal,
lineage, and evidence metadata are retained deliberately. One successful cloud
restore does not establish universal safety, scale, novelty, or compliance.
The previous paired local PostgreSQL suite is a separate experiment.
'''
    path=ROOT/'docs'/'FINAL_CLOUD_EVALUATION.md'
    path.write_text(text,encoding='utf-8')
    return path


def evaluate(sess):
    before=free_guard(sess)
    cf=sdk(sess,'cloudformation')
    _,outputs=stack_outputs(cf,STACK)
    _,core=stack_outputs(cf,'rewindguard-x-core')
    base=outputs['ApiUrl']
    cognito=sdk(sess,'cognito-idp')
    username='verify_'+uuid4().hex
    password='Aa1!'+secrets.token_urlsafe(24)
    checks={}
    result={'measured_at':datetime.now(timezone.utc).isoformat(),'checks':checks,
            'credit_before':before['accountPlanRemainingCredits']}
    created=False
    try:
        cognito.admin_create_user(UserPoolId=outputs['UserPoolId'],Username=username,MessageAction='SUPPRESS',
                    UserAttributes=[{'Name':'custom:tenant_id','Value':'synthetic'}])
        created=True
        cognito.admin_set_user_password(UserPoolId=outputs['UserPoolId'],Username=username,Password=password,Permanent=True)
        cognito.admin_add_user_to_group(UserPoolId=outputs['UserPoolId'],Username=username,GroupName='operators')
        tokens=cognito.initiate_auth(ClientId=outputs['UserPoolClientId'],AuthFlow='USER_PASSWORD_AUTH',
                                     AuthParameters={'USERNAME':username,'PASSWORD':password})['AuthenticationResult']
        token=tokens['IdToken']
        expires=time.time()+tokens['ExpiresIn']
        refresh=tokens['RefreshToken']
        def renewed():
            nonlocal token,expires
            if time.time()>expires-60:
                update=cognito.initiate_auth(ClientId=outputs['UserPoolClientId'],AuthFlow='REFRESH_TOKEN_AUTH',
                             AuthParameters={'REFRESH_TOKEN':refresh})['AuthenticationResult']
                token=update['IdToken']; expires=time.time()+update['ExpiresIn']
            return token
        response,_=call(base,None,'/v1/me')
        checks['anonymous_request_denied']=response.status_code==401
        response,_=call(base,'invalid.jwt.token','/v1/me')
        checks['forged_token_denied']=response.status_code==401
        response,_=call(base,renewed(),'/v1/me')
        checks['authenticated_operator']=response.status_code==200 and response.json().get('operator') is True
        response,_=call(base,renewed(),'/v1/erase-requests',{'tenant_id':'another','subject_id':'test'})
        checks['cross_tenant_request_denied']=response.status_code==403
        subject='cloud_'+uuid4().hex
        scope={'tenant_id':'synthetic','subject_id':subject,'subject_generation':1}
        response,_=call(base,renewed(),'/v1/lab/seed',{'subject_id':subject,'request_id':'seed_'+uuid4().hex})
        if response.status_code!=200: raise RuntimeError('Cloud seed failed: HTTP '+str(response.status_code))
        checks['rds_profile_and_index_seeded']=response.json().get('seeded') and response.json().get('index_hits',0)>0
        reads=[]
        for _ in range(10):
            response,duration=call(base,renewed(),'/v1/profile?subject_id='+subject)
            if response.status_code!=200 or not response.json(): raise RuntimeError('Seeded profile read failed')
            reads.append(duration*1000)
            time.sleep(.55)
        result.update(read_samples=len(reads),read_median_ms=round(statistics.median(reads),2),read_p95_ms=round(sorted(reads)[-1],2))
        start=time.perf_counter()
        response,_=call(base,renewed(),'/v1/lab/recovery',{'subject_id':subject})
        if response.status_code!=202: raise RuntimeError('Recovery start failed: HTTP '+str(response.status_code))
        run=response.json()['run_id']
        print('Cloud snapshot/recovery workflow started. Private RDS restore can take 15–40 minutes.',flush=True)
        status=None
        for attempt in range(360):
            time.sleep(10)
            response,_=call(base,renewed(),'/v1/runs/'+run)
            if response.status_code!=200: raise RuntimeError('Recovery status request failed')
            status=response.json()
            if attempt%6==0: print('Recovery status: '+status['status'],flush=True)
            if status['status']!='RUNNING': break
        result['workflow_seconds']=round(time.perf_counter()-start,2)
        if not status or status['status']!='SUCCEEDED': raise RuntimeError('Recovery was held or exceeded its evaluation window')
        outcome=status['result']
        result['workflow_result']=outcome
        checks['old_snapshot_contains_seeded_data']=outcome.get('raw_before',{}).get('profiles',0)>0
        checks['guarded_candidate_read_denied_before_reconciliation']=outcome.get('guarded_before') is None
        checks['candidate_reconciled']=all(value==0 for value in outcome.get('raw_after',{}).values()) and bool(outcome.get('raw_after'))
        checks['recovery_published']=outcome.get('release',{}).get('state')=='RELEASED'
        for _ in range(12):
            response,_=call(base,renewed(),'/v1/profile?subject_id='+subject)
            if response.status_code==200: break
            time.sleep(5)
        checks['published_api_does_not_expose_erased_profile']=response.status_code==200 and response.json() is None
        s3=sdk(sess,'s3')
        envelope=json.loads(s3.get_object(Bucket=core['DocumentBucketName'],Key='evidence/'+run+'/recovery.json')['Body'].read())['evidence']
        verifier=KmsEvidenceSigner(sdk(sess,'kms'),outputs['EvidenceKeyArn'])
        checks['real_kms_signature_valid']=verifier.verify(envelope)
        forged=copy.deepcopy(envelope); forged['certificate']['decision']='FORGED'
        checks['evidence_tamper_rejected']=not verifier.verify(forged)
        result['public_key_fingerprint']=__import__('hashlib').sha256(base64.b64decode(envelope['public_key_spki_der'])).hexdigest()
        state=sdk(sess,'dynamodb').get_item(TableName=outputs['ControlTableName'],Key={'pk':{'S':'ACTIVE'}},ConsistentRead=True)['Item']
        active=json.loads(state['value']['S'])
        database=sdk(sess,'rds').describe_db_instances(DBInstanceIdentifier=active['db_id'])['DBInstances'][0]
        checks['published_database_is_private']=database['PubliclyAccessible'] is False
        checks['database_storage_encrypted']=database['StorageEncrypted'] is True
        checks['iam_database_authentication_enabled']=database['IAMDatabaseAuthenticationEnabled'] is True
        result['database_configuration']={k:database.get(k) for k in ('Engine','EngineVersion','DBInstanceClass','AllocatedStorage','MultiAZ','PubliclyAccessible','StorageEncrypted')}
        after=free_guard(sess)
        result['credit_after']=after['accountPlanRemainingCredits']
        result['observed_credit_delta_usd']=round(before['accountPlanRemainingCredits']['amount']-after['accountPlanRemainingCredits']['amount'],6)
        result['all_checks_passed']=all(checks.values())
    finally:
        if created:
            try:
                cognito.admin_user_global_sign_out(UserPoolId=outputs['UserPoolId'],Username=username)
            finally:
                cognito.admin_delete_user(UserPoolId=outputs['UserPoolId'],Username=username)
    destination=ROOT/'evidence'/'private'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'cloud-evaluation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    path=report_file(result)
    print(json.dumps({'all_checks_passed':result['all_checks_passed'],'checks':checks,'report':str(path)},indent=2),flush=True)
    if not result['all_checks_passed']: raise RuntimeError('One or more cloud acceptance checks failed; inspect the report')
    return result


if __name__=='__main__':
    evaluate(session())
