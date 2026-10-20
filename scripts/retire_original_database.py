"""Preview retirement of only the superseded synthetic original DB. Execution requires --execute."""
import argparse
import json
from pathlib import Path
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard,wait_stack
from infra.build_application import template


def retire_original(sess,execute=False):
    free_guard(sess)
    cf=sdk(sess,'cloudformation')
    info,outputs=stack_outputs(cf,STACK)
    parameters={x['ParameterKey']:x.get('ParameterValue') for x in info['Parameters']}
    if info['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE'} or parameters['EnableBootstrap']!='false':
        raise RuntimeError('A stable stack with bootstrap disabled is required')
    item=sdk(sess,'dynamodb').get_item(TableName=outputs['ControlTableName'],Key={'pk':{'S':'ACTIVE'}},ConsistentRead=True).get('Item')
    active=json.loads(item['value']['S']) if item else {}
    if not active.get('db_id','').startswith('rgx-candidate-'):
        raise RuntimeError('Original database cannot be retired before a candidate is published')
    rds=sdk(sess,'rds')
    current=rds.describe_db_instances(DBInstanceIdentifier=active['db_id'])['DBInstances'][0]
    original=rds.describe_db_instances(DBInstanceIdentifier='rgx-live')['DBInstances'][0]
    tags={t['Key']:t['Value'] for t in rds.list_tags_for_resource(ResourceName=original['DBInstanceArn'])['TagList']}
    if tags.get('Project')!='rewindguard-x' or original.get('PubliclyAccessible') or not original.get('StorageEncrypted'):
        raise RuntimeError('Original synthetic resource identity check failed')
    if current.get('PubliclyAccessible') or not current.get('StorageEncrypted'):
        raise RuntimeError('Published candidate identity check failed')
    preview={'database_to_delete':'rgx-live','preserve_published_database':active['db_id'],'final_snapshot':False,'effect':'Permanently delete superseded synthetic original database through CloudFormation; free one RDS slot. No paid upgrade.'}
    print(json.dumps(preview,indent=2),flush=True)
    if not execute: return preview
    updates=[{'ParameterKey':x['ParameterKey'],'UsePreviousValue':True} for x in info['Parameters'] if x['ParameterKey']!='RetainOriginalDatabase']
    updates.append({'ParameterKey':'RetainOriginalDatabase','ParameterValue':'false'})
    cf.update_stack(StackName=STACK,TemplateBody=json.dumps(template()),Parameters=updates,Capabilities=['CAPABILITY_IAM'])
    wait_stack(cf,'update')
    return preview


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    retire_original(session(),args.execute)
