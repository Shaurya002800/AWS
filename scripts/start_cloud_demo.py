"""Resume the private database after the idle cleanup has stopped it."""
import json
import time
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard


if __name__=='__main__':
    sess=session(); free_guard(sess)
    _,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    item=sdk(sess,'dynamodb').get_item(TableName=outputs['ControlTableName'],Key={'pk':{'S':'ACTIVE'}},ConsistentRead=True).get('Item')
    active=json.loads(item['value']['S']) if item else {'db_id':outputs['DatabaseId']}
    rds=sdk(sess,'rds')
    database=rds.describe_db_instances(DBInstanceIdentifier=active['db_id'])['DBInstances'][0]
    tags={x['Key']:x['Value'] for x in rds.list_tags_for_resource(ResourceName=database['DBInstanceArn'])['TagList']}
    if tags.get('Project')!='rewindguard-x': raise SystemExit('Database ownership check failed')
    rds.add_tags_to_resource(ResourceName=database['DBInstanceArn'],Tags=[{'Key':'ExpiresAt','Value':str(int(time.time())+7200)}])
    if database['DBInstanceStatus']=='stopped':
        rds.start_db_instance(DBInstanceIdentifier=active['db_id'])
        print('Starting the private database; this can take several minutes.',flush=True)
        rds.get_waiter('db_instance_available').wait(DBInstanceIdentifier=active['db_id'])
    elif database['DBInstanceStatus']!='available':
        raise SystemExit('Database is currently '+database['DBInstanceStatus']+'; retry after it settles')
    print('Demo ready for the next two hours: '+outputs['ApiUrl']+'/ui')
