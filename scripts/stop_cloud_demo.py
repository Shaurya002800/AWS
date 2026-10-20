"""Stop tagged demo databases immediately and remove disposable synthetic snapshots."""
from scripts.deploy_application import session,sdk

def stop(sess):
    rds=sdk(sess,'rds')
    actions=[]
    for page in rds.get_paginator('describe_db_instances').paginate():
        for item in page['DBInstances']:
            name=item['DBInstanceIdentifier']
            if name!='rgx-live' and not name.startswith('rgx-candidate-'): continue
            tags={t['Key']:t['Value'] for t in rds.list_tags_for_resource(ResourceName=item['DBInstanceArn'])['TagList']}
            if tags.get('Project')!='rewindguard-x': continue
            if item['DBInstanceStatus']=='available':
                rds.stop_db_instance(DBInstanceIdentifier=name)
                actions.append('database stop requested')
    for page in rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual'):
        for item in page['DBSnapshots']:
            if not item['DBSnapshotIdentifier'].startswith('rgx-snapshot-') or item['Status']!='available': continue
            tags={t['Key']:t['Value'] for t in rds.list_tags_for_resource(ResourceName=item['DBSnapshotArn'])['TagList']}
            if tags.get('Project')=='rewindguard-x' and tags.get('Disposable')=='true':
                rds.delete_db_snapshot(DBSnapshotIdentifier=item['DBSnapshotIdentifier'])
                actions.append('disposable synthetic snapshot deletion requested')
    logs=sdk(sess,'logs')
    for page in logs.get_paginator('describe_log_groups').paginate(logGroupNamePrefix='/aws/rds/instance/rgx-'):
        for group in page['logGroups']:
            logs.put_retention_policy(logGroupName=group['logGroupName'],retentionInDays=1)
    print({'cost_control_actions':actions},flush=True)
    return actions

if __name__=='__main__':
    stop(session())
