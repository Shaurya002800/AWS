"""Resume only the approved serving database for a bounded review window."""
import argparse
import json
import time
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard


def start_demo(sess,minutes=30):
    if type(minutes) is not int or not 15<=minutes<=120:
        raise ValueError('Review window must be between 15 and 120 minutes')
    free_guard(sess)
    _,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    item=sdk(sess,'dynamodb').get_item(TableName=outputs['ControlTableName'],Key={'pk':{'S':'ACTIVE'}},ConsistentRead=True).get('Item')
    active=json.loads(item['value']['S']) if item else {'db_id':outputs['DatabaseId']}
    rds=sdk(sess,'rds')
    database=rds.describe_db_instances(DBInstanceIdentifier=active['db_id'])['DBInstances'][0]
    tags={x['Key']:x['Value'] for x in rds.list_tags_for_resource(ResourceName=database['DBInstanceArn'])['TagList']}
    if tags.get('Project')!='rewindguard-x' or database.get('PubliclyAccessible') or not database.get('StorageEncrypted'):
        raise RuntimeError('Database ownership/private/encryption check failed')
    state=database['DBInstanceStatus']
    if state=='stopping': raise RuntimeError('Database stop is still in progress. Retry after it finishes.')
    if state=='stopped':
        rds.start_db_instance(DBInstanceIdentifier=active['db_id'])
        print('Starting the approved private database; this can take several minutes.',flush=True)
    if state!='available':
        rds.get_waiter('db_instance_available').wait(DBInstanceIdentifier=active['db_id'],WaiterConfig={'Delay':15,'MaxAttempts':120})
    expires=int(time.time())+minutes*60
    rds.add_tags_to_resource(ResourceName=database['DBInstanceArn'],Tags=[{'Key':'ExpiresAt','Value':str(expires)}])
    result={'ui_url':outputs['ApiUrl']+'/ui','review_window_minutes':minutes,'expires_at':expires,'cleanup_interval_minutes':30,'database_status':'available'}
    print(json.dumps(result,indent=2),flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--minutes',type=int,default=30)
    args=parser.parse_args()
    start_demo(session(),args.minutes)
