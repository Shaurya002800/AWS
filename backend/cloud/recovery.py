"""Trusted RDS lifecycle and epoch-conditioned publication.

Only configured subnet/security groups are used. No input can supply a host,
security group, connection string, or arbitrary snapshot ARN.
"""
import json
import os
import time
from backend.cloud.common import client,authority,identifier,canonical,invoke
from backend.cloud.runtime import erase,validate
from backend.eraselock.domain import RecoveryState,StaleEpoch
from contracts.adapter_protocol import SubjectScope

PROJECT='rewindguard-x'


class RecoveryCapacityUnavailable(RuntimeError):
    pass


def ensure_restore_capacity(rds):
    # The verified Free plan permits two instances, including stopped ones.
    # Refuse before snapshot/erasure; never delete a database or upgrade a plan.
    instances=[item for page in rds.get_paginator('describe_db_instances').paginate() for item in page['DBInstances']]
    if len(instances)>=2:
        raise RecoveryCapacityUnavailable('A free RDS instance slot is required before recovery can start')


def get_record(pk):
    item=client('dynamodb').get_item(TableName=os.environ['CONTROL_TABLE'],Key={'pk':{'S':pk}},ConsistentRead=True).get('Item')
    return json.loads(item['value']['S']) if item else None


def put_record(pk,value):
    client('dynamodb').put_item(TableName=os.environ['CONTROL_TABLE'],Item={'pk':{'S':pk},'value':{'S':json.dumps(value,sort_keys=True)}})


def db(instance):
    return client('rds').describe_db_instances(DBInstanceIdentifier=instance)['DBInstances'][0]


def active_db():
    active=get_record('ACTIVE')
    return active['db_id'] if active else os.environ['LIVE_DB_ID']


def register_candidate(candidate_id,tenant_id):
    candidate_id=identifier(candidate_id)
    if not candidate_id.startswith('rgx-candidate-'):
        raise ValueError('Unexpected candidate identifier')
    data=db(candidate_id)
    groups={x['VpcSecurityGroupId'] for x in data['VpcSecurityGroups']}
    tags={x['Key']:x['Value'] for x in client('rds').list_tags_for_resource(ResourceName=data['DBInstanceArn'])['TagList']}
    if (data['DBInstanceStatus']!='available' or data.get('PubliclyAccessible') or
            groups!={os.environ['CANDIDATE_DB_SG']} or
            data['DBSubnetGroup']['DBSubnetGroupName']!=os.environ['DB_SUBNET_GROUP'] or
            tags.get('Project')!=PROJECT or tags.get('Tenant')!=tenant_id):
        raise ValueError('Candidate failed private-resource identity checks')
    value={'candidate_id':candidate_id,'tenant_id':tenant_id,'endpoint':data['Endpoint']['Address'],
           'db_id':candidate_id,'resource_id':data['DbiResourceId'],'status':'ISOLATED'}
    put_record('CANDIDATE#'+candidate_id,value)
    return value


def publish(candidate_id,scope_digest):
    record=get_record('CANDIDATE#'+identifier(candidate_id))
    if not record or record['status']!='VALIDATED' or record.get('scope_digest')!=scope_digest:
        raise ValueError('Candidate lacks matching trusted validation')
    auth=authority()
    approval=auth.release_recovery(candidate_id,expected_scope_digest=scope_digest)
    # Serialize the route pointer with the authority version. Any intervening
    # erasure or permit mutation causes this transaction to fail, retaining HOLD.
    version,snapshot=auth._read()
    if snapshot.epoch(approval.tenant_id)!=approval.validated_epoch or snapshot._has_pending(approval.tenant_id):
        raise StaleEpoch('Policy moved before publication')
    if snapshot._recoveries[candidate_id].state!=RecoveryState.RELEASED:
        raise ValueError('Approval is not released')
    current=db(candidate_id)
    if current['DbiResourceId']!=record['resource_id'] or current.get('PubliclyAccessible'):
        raise ValueError('Candidate identity changed after validation')
    client('rds').modify_db_instance(DBInstanceIdentifier=candidate_id,
                      VpcSecurityGroupIds=[os.environ['SERVING_DB_SG']],ApplyImmediately=True)
    active={'endpoint':record['endpoint'],'db_id':candidate_id,'tenant_id':record['tenant_id'],
            'approval_epoch':approval.validated_epoch,'candidate_id':candidate_id}
    try:
        client('dynamodb').transact_write_items(TransactItems=[
            {'ConditionCheck':{'TableName':os.environ['AUTHORITY_TABLE'],'Key':auth._KEY,
               'ConditionExpression':'#v = :v','ExpressionAttributeNames':{'#v':'version'},
               'ExpressionAttributeValues':{':v':{'N':str(version)}}}},
            {'Put':{'TableName':os.environ['CONTROL_TABLE'],'Item':{'pk':{'S':'ACTIVE'},'value':{'S':json.dumps(active)}}}},
        ])
    except Exception:
        client('rds').modify_db_instance(DBInstanceIdentifier=candidate_id,
                        VpcSecurityGroupIds=[os.environ['CANDIDATE_DB_SG']],ApplyImmediately=True)
        raise
    put_record('CANDIDATE#'+candidate_id,{**record,'status':'RELEASED'})
    return {'state':'RELEASED','validated_epoch':approval.validated_epoch}


def handler(event,context):
    action=event['action']
    run=identifier(event['run_id'])
    tenant=identifier(event['tenant_id'])
    scope=SubjectScope(**event['scope'])
    if scope.tenant_id!=tenant:
        raise ValueError('Scope tenant mismatch')
    snapshot_id='rgx-snapshot-'+run
    candidate_id='rgx-candidate-'+run
    rds=client('rds')
    if action=='snapshot':
        ensure_restore_capacity(rds)
        live=active_db()
        if db(live)['DBInstanceStatus']!='available': raise RuntimeError('Database is in standby; start it before a demo')
        try:
            rds.create_db_snapshot(DBSnapshotIdentifier=snapshot_id,DBInstanceIdentifier=live,
                 Tags=[{'Key':'Project','Value':PROJECT},{'Key':'Tenant','Value':tenant},
                       {'Key':'Disposable','Value':'true'},{'Key':'ExpiresAt','Value':str(int(time.time())+7200)}])
        except rds.exceptions.ClientError as error:
            if error.response['Error']['Code']!='DBSnapshotAlreadyExists': raise
        return {**event,'snapshot_id':snapshot_id,'candidate_id':candidate_id}
    if action=='snapshot_status':
        status=rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)['DBSnapshots'][0]['Status']
        return {**event,'ready':status=='available'}
    if action=='erase':
        result=erase(scope,'erase_'+run)
        if result['state']!='VERIFIED': raise RuntimeError('Erasure verification held')
        client('s3').put_object(Bucket=os.environ['DOCUMENT_BUCKET'],Key='evidence/'+run+'/erasure.json',Body=canonical(result),ContentType='application/json')
        return {**event,'erasure_state':result['state']}
    if action=='restore':
        try:
            rds.restore_db_instance_from_db_snapshot(DBInstanceIdentifier=candidate_id,DBSnapshotIdentifier=snapshot_id,
                DBInstanceClass='db.t4g.micro',DBSubnetGroupName=os.environ['DB_SUBNET_GROUP'],
                VpcSecurityGroupIds=[os.environ['CANDIDATE_DB_SG']],PubliclyAccessible=False,MultiAZ=False,
                EnableIAMDatabaseAuthentication=True,AvailabilityZone=os.environ['DB_AZ'],
                Tags=[{'Key':'Project','Value':PROJECT},{'Key':'Tenant','Value':tenant},{'Key':'Disposable','Value':'true'},
                      {'Key':'ExpiresAt','Value':str(int(time.time())+7200)}])
        except rds.exceptions.ClientError as error:
            if error.response['Error']['Code']!='DBInstanceAlreadyExists': raise
        return event
    if action=='restore_status':
        status=db(candidate_id)['DBInstanceStatus']
        return {**event,'ready':status=='available'}
    if action=='validate':
        register_candidate(candidate_id,tenant)
        oracle=invoke(os.environ['CANDIDATE_FUNCTION'],{'action':'oracle','scope':event['scope'],'candidate_id':candidate_id})
        result=validate(candidate_id,tenant)
        if not result['approved']: raise RuntimeError('Candidate validation held')
        record=get_record('CANDIDATE#'+candidate_id)
        put_record('CANDIDATE#'+candidate_id,{**record,'status':'VALIDATED','scope_digest':result['scope_digest']})
        after=invoke(os.environ['CANDIDATE_FUNCTION'],{'action':'oracle','scope':event['scope'],'candidate_id':candidate_id})
        client('s3').put_object(Bucket=os.environ['DOCUMENT_BUCKET'],Key='evidence/'+run+'/recovery.json',Body=canonical(result),ContentType='application/json')
        return {**event,'scope_digest':result['scope_digest'],'raw_before':oracle['raw_counts'],
                'guarded_before':oracle['guarded_profile'],'raw_after':after['raw_counts'],
                'evidence_signed':True}
    if action=='release':
        result=publish(candidate_id,event['scope_digest'])
        summary={**event,'release':result,'finished_at':int(time.time())}
        client('s3').put_object(Bucket=os.environ['DOCUMENT_BUCKET'],Key='evidence/'+run+'/summary.json',Body=canonical(summary),ContentType='application/json')
        return summary
    raise ValueError('Unknown recovery action')
