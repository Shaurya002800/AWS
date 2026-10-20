"""Verify the final live checkpoint and reconcile tested code into CloudFormation."""
import json
import time
from pathlib import Path
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard
from scripts.sync_cloud_code import sync

s=session()
cf=sdk(s,'cloudformation')
print('Verifying completed cloud evaluation and stack cleanup',flush=True)
result=json.loads(Path('evidence/private/cloud-evaluation.json').read_text())
assert result['all_checks_passed']
for attempt in range(120):
    info,outputs=stack_outputs(cf,STACK)
    if info['StackStatus'] in {'CREATE_COMPLETE','UPDATE_COMPLETE'}: break
    if info['StackStatus']!='UPDATE_COMPLETE_CLEANUP_IN_PROGRESS': raise RuntimeError('Inspect stack status: '+info['StackStatus'])
    if attempt%4==0: print('Waiting for AWS bootstrap network cleanup',flush=True)
    time.sleep(15)
else: raise RuntimeError('Stack cleanup exceeded verification window')
patch=json.loads(Path('evidence/private/code-patch.json').read_text())
parameters={p['ParameterKey']:p.get('ParameterValue') for p in info['Parameters']}
if parameters['CodeKey']!=patch['key'] or parameters['CodeVersion']!=patch['version']:
    sync(s)
info,outputs=stack_outputs(cf,STACK)
assert outputs['BootstrapFunctionName']=='disabled'
rds=sdk(s,'rds')
for attempt in range(100):
    owned=[]
    for item in rds.describe_db_instances()['DBInstances']:
        if item['DBInstanceIdentifier']!='rgx-live' and not item['DBInstanceIdentifier'].startswith('rgx-candidate-'): continue
        tags={t['Key']:t['Value'] for t in rds.list_tags_for_resource(ResourceName=item['DBInstanceArn'])['TagList']}
        if tags.get('Project')=='rewindguard-x': owned.append(item)
    if owned and all(x['DBInstanceStatus']=='stopped' for x in owned): break
    for item in owned:
        if item['DBInstanceStatus']=='available': rds.stop_db_instance(DBInstanceIdentifier=item['DBInstanceIdentifier'])
    if attempt%4==0: print('Waiting for database standby confirmation',flush=True)
    time.sleep(15)
else: raise RuntimeError('Database standby exceeded verification window')
plan=free_guard(s)
manual=[x for page in rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual') for x in page['DBSnapshots'] if x['DBSnapshotIdentifier'].startswith('rgx-snapshot-')]
checkpoint={'stack_status':info['StackStatus'],'bootstrap_disabled':True,'database_count':len(owned),'all_databases_stopped':True,'remaining_synthetic_snapshots':len(manual),'free_plan_active':True,'positive_credits':True,'all_evaluation_checks_passed':True,'measured_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
Path('evidence/private/final-cloud-checkpoint.json').write_text(json.dumps(checkpoint,indent=2))
print(json.dumps(checkpoint,indent=2),flush=True)
