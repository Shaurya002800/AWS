"""Stop idle databases and reclaim expired or superseded disposable candidates."""
import json
import os
import time
from backend.cloud.common import client
from backend.cloud.recovery import get_record


def handler(event,context):
    rds=client('rds')
    now=int(time.time())
    active=get_record('ACTIVE')
    active_id=active.get('db_id') if active else None
    results=[]
    for page in rds.get_paginator('describe_db_instances').paginate():
        for item in page['DBInstances']:
            name=item['DBInstanceIdentifier']
            if name!='rgx-live' and not name.startswith('rgx-candidate-'): continue
            tags={x['Key']:x['Value'] for x in rds.list_tags_for_resource(ResourceName=item['DBInstanceArn'])['TagList']}
            if tags.get('Project')!='rewindguard-x': continue
            expired=int(tags.get('ExpiresAt',str(now+1)))<=now
            retired=False
            if name!=active_id and name.startswith('rgx-candidate-') and tags.get('Disposable')=='true':
                record=get_record('CANDIDATE#'+name)
                retired=bool(record and record.get('status')=='RELEASED')
            if not expired and not retired: continue
            state=item['DBInstanceStatus']
            if name==active_id and state=='available':
                rds.stop_db_instance(DBInstanceIdentifier=name)
                results.append({'kind':'idle_serving_database_stopped'})
            elif name.startswith('rgx-candidate-') and tags.get('Disposable')=='true' and state in {'available','stopped'}:
                # Publication can advance while a cleanup pass is running.
                # Re-read the pointer after observing a RELEASED record.
                latest=get_record('ACTIVE')
                if not latest or latest.get('db_id')==name: continue
                rds.delete_db_instance(DBInstanceIdentifier=name,SkipFinalSnapshot=True,DeleteAutomatedBackups=True)
                results.append({'kind':'superseded_candidate_deleted' if retired else 'expired_candidate_deleted'})
            elif name=='rgx-live' and state=='available':
                rds.stop_db_instance(DBInstanceIdentifier=name)
                results.append({'kind':'idle_live_database_stopped'})
    for page in rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual'):
        for item in page['DBSnapshots']:
            name=item['DBSnapshotIdentifier']
            if not name.startswith('rgx-snapshot-') or item['Status']!='available': continue
            tags={x['Key']:x['Value'] for x in rds.list_tags_for_resource(ResourceName=item['DBSnapshotArn'])['TagList']}
            if tags.get('Project')=='rewindguard-x' and tags.get('Disposable')=='true' and int(tags.get('ExpiresAt',str(now+1)))<=now:
                rds.delete_db_snapshot(DBSnapshotIdentifier=name)
                results.append({'kind':'expired_synthetic_snapshot_deleted'})
    print(json.dumps({'event':'cleanup','actions':results}))
    return {'actions':results}
