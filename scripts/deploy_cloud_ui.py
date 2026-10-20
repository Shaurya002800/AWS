"""Publish UI code into the existing stack; never resumes or creates a database."""
import hashlib
import json
from pathlib import Path
import httpx
from scripts.deploy_application import session,sdk,stack_outputs,bundle,STACK,free_guard,wait_stack


def deploy_ui(sess):
    free_guard(sess)
    cf=sdk(sess,'cloudformation')
    info,_=stack_outputs(cf,STACK)
    if info['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE'}:
        raise RuntimeError('Inspect the existing stack before updating UI')
    params={p['ParameterKey']:p.get('ParameterValue') for p in info['Parameters']}
    if params.get('EnableBootstrap')!='false':
        raise RuntimeError('UI update requires bootstrap already disabled')
    _,core=stack_outputs(cf,'rewindguard-x-core')
    payload=bundle()
    key='deployments/cloud-'+hashlib.sha256(payload.read_bytes()).hexdigest()[:24]+'.zip'
    upload=sdk(sess,'s3').put_object(Bucket=core['DocumentBucketName'],Key=key,Body=payload.read_bytes(),ContentType='application/zip')
    updates={'CodeKey':key,'CodeVersion':upload['VersionId']}
    parameters=[{'ParameterKey':p['ParameterKey'],'ParameterValue':updates[p['ParameterKey']]} if p['ParameterKey'] in updates else {'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in info['Parameters']]
    print('Publishing notebook UI to the existing application stack',flush=True)
    cf.update_stack(StackName=STACK,UsePreviousTemplate=True,Parameters=parameters,Capabilities=['CAPABILITY_IAM'])
    wait_stack(cf,'update')
    info,outputs=stack_outputs(cf,STACK)
    base=outputs['ApiUrl']
    checks={}
    for route,expected in [('/ui','Old backups.'),('/style.css','--paper:'),('/app.js','function renderRun')]:
        response=httpx.get(base+route,timeout=25)
        checks[route]=response.status_code==200 and expected in response.text
    databases=[x for x in sdk(sess,'rds').describe_db_instances()['DBInstances'] if x['DBInstanceIdentifier']=='rgx-live' or x['DBInstanceIdentifier'].startswith('rgx-candidate-')]
    state={'stack_status':info['StackStatus'],'ui_url':base+'/ui','asset_checks':checks,'databases_remain_stopped':bool(databases) and all(x['DBInstanceStatus']=='stopped' for x in databases),'bootstrap_disabled':outputs['BootstrapFunctionName']=='disabled'}
    free_guard(sess)
    Path('evidence/private/ui-deployment.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps(state,indent=2),flush=True)
    if not all(checks.values()): raise RuntimeError('Hosted asset smoke check failed')
    return state

if __name__=='__main__':
    deploy_ui(session())
