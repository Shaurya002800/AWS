"""Check the current approved serving database without creating a restore candidate."""
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import secrets
from uuid import uuid4
import httpx
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard
from backend.cloud.signing import KmsEvidenceSigner


def check_review(sess):
    free_guard(sess)
    _,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    _,core=stack_outputs(sdk(sess,'cloudformation'),'rewindguard-x-core')
    control=sdk(sess,'dynamodb').get_item(TableName=outputs['ControlTableName'],Key={'pk':{'S':'ACTIVE'}},ConsistentRead=True)['Item']
    active=json.loads(control['value']['S'])
    print('Waiting for the approved serving database, then checking current authenticated paths',flush=True)
    sdk(sess,'rds').get_waiter('db_instance_available').wait(DBInstanceIdentifier=active['db_id'],WaiterConfig={'Delay':15,'MaxAttempts':120})
    cog=sdk(sess,'cognito-idp')
    username='reviewcheck_'+uuid4().hex
    password='Aa7!'+secrets.token_urlsafe(24)
    subject='review_'+uuid4().hex
    checks={}
    result={'measured_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'scope':'current approved serving database; no new snapshot/restore'}
    created=False
    try:
        cog.admin_create_user(UserPoolId=outputs['UserPoolId'],Username=username,MessageAction='SUPPRESS',UserAttributes=[{'Name':'custom:tenant_id','Value':'synthetic'}])
        created=True
        cog.admin_set_user_password(UserPoolId=outputs['UserPoolId'],Username=username,Password=password,Permanent=True)
        cog.admin_add_user_to_group(UserPoolId=outputs['UserPoolId'],Username=username,GroupName='operators')
        token=cog.initiate_auth(ClientId=outputs['UserPoolClientId'],AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':username,'PASSWORD':password})['AuthenticationResult']['IdToken']
        with httpx.Client(base_url=outputs['ApiUrl'],headers={'Authorization':'Bearer '+token},timeout=60) as api:
            me=api.get('/v1/me'); checks['authenticated_operator']=me.status_code==200 and me.json().get('operator') is True
            if not checks['authenticated_operator']: raise RuntimeError('Authenticated operator check failed')
            seeded=api.post('/v1/lab/seed',json={'subject_id':subject,'request_id':'reviewseed_'+uuid4().hex})
            checks['current_database_seed_and_index']=seeded.status_code==200 and seeded.json().get('seeded') and seeded.json().get('index_hits',0)>0
            if not checks['current_database_seed_and_index']: raise RuntimeError('Current serving database seed failed')
            profile=api.get('/v1/profile',params={'subject_id':subject})
            checks['published_database_real_profile_read']=profile.status_code==200 and bool(profile.json())
            erased=api.post('/v1/erase-requests',json={'subject_id':subject,'request_id':'reviewerase_'+uuid4().hex})
            checks['current_erasure_verified']=erased.status_code==200 and erased.json().get('state')=='VERIFIED'
            if not checks['current_erasure_verified']: raise RuntimeError('Current erasure check failed')
            evidence=erased.json()['evidence']
            signer=KmsEvidenceSigner(sdk(sess,'kms'),outputs['EvidenceKeyArn'])
            checks['current_kms_signature_verified']=signer.verify(evidence)
            forged=copy.deepcopy(evidence); forged['certificate']['authority_epoch']=999999
            checks['current_evidence_tamper_rejected']=not signer.verify(forged)
            profile=api.get('/v1/profile',params={'subject_id':subject})
            checks['erased_profile_unavailable']=profile.status_code==200 and profile.json() is None
            rejected=api.post('/v1/erase-requests',json={'tenant_id':'another','subject_id':subject})
            checks['cross_tenant_denied']=rejected.status_code==403
        prefix='synthetic/'+subject+'/g1/'
        objects=sdk(sess,'s3').list_object_versions(Bucket=core['DocumentBucketName'],Prefix=prefix)
        checks['erased_object_versions_removed']=not objects.get('Versions') and not objects.get('DeleteMarkers')
        result['all_checks_passed']=all(checks.values())
        free_guard(sess)
    finally:
        if created: cog.admin_delete_user(UserPoolId=outputs['UserPoolId'],Username=username)
        result['temporary_test_login_removed']=created
        Path('evidence/private/review-readiness.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    if not result['all_checks_passed']: raise RuntimeError('Review readiness check failed')
    return result

if __name__=='__main__':
    check_review(session())
