"""Read-only verification: never deploy stale bundles or stop a review session."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from scripts.deploy_application import session, sdk, stack_outputs, STACK, free_guard


def verify_checkpoint(sess, *, allow_running=False):
    result=json.loads(Path('evidence/private/cloud-evaluation.json').read_text())
    if not result.get('all_checks_passed'):
        raise RuntimeError('The full cloud evaluation has not passed')
    free_guard(sess)
    info,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    if info['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE'}:
        raise RuntimeError('Inspect stack status: '+info['StackStatus'])
    if outputs['BootstrapFunctionName']!='disabled':
        raise RuntimeError('Bootstrap must remain disabled')
    rds=sdk(sess,'rds')
    owned=[]
    for item in rds.describe_db_instances()['DBInstances']:
        name=item['DBInstanceIdentifier']
        if name!='rgx-live' and not name.startswith('rgx-candidate-'): continue
        tags={t['Key']:t['Value'] for t in rds.list_tags_for_resource(ResourceName=item['DBInstanceArn'])['TagList']}
        if tags.get('Project')=='rewindguard-x': owned.append(item)
    stopped=bool(owned) and all(x['DBInstanceStatus']=='stopped' for x in owned)
    if not owned or (not allow_running and not stopped):
        raise RuntimeError('Databases are not in standby; use stop_cloud_demo after reviewing, or --allow-running to inspect a review window')
    manual=[x for page in rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual') for x in page['DBSnapshots'] if x['DBSnapshotIdentifier'].startswith('rgx-snapshot-')]
    checkpoint={'stack_status':info['StackStatus'],'bootstrap_disabled':True,'database_count':len(owned),'all_databases_stopped':stopped,'remaining_synthetic_snapshots':len(manual),'free_plan_active':True,'positive_credits':True,'all_evaluation_checks_passed':True,'measured_at':datetime.now(timezone.utc).isoformat()}
    Path('evidence/private/final-cloud-checkpoint.json').write_text(json.dumps(checkpoint,indent=2))
    print(json.dumps(checkpoint,indent=2),flush=True)
    return checkpoint


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-running',action='store_true',help='Inspect without stopping an intentional review window')
    args=parser.parse_args()
    verify_checkpoint(session(),allow_running=args.allow_running)
