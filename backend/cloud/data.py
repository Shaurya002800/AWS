"""Private VPC PostgreSQL worker; no public network endpoint or caller-selected DSN."""
import json
import os
from dataclasses import asdict
from pathlib import Path
from hashlib import sha256

import psycopg
from botocore.exceptions import ClientError
from psycopg.conninfo import make_conninfo

from backend.cloud.common import client,authority,identifier,success
from backend.adapters.postgres import PostgresAdapter,migrate
from backend.adapters.search_index import PostgresSearchIndex
from backend.adapters.aws_s3 import AwsS3Adapter
from backend.eraselock.domain import NotFound,AdmissionClosed,Conflict
from contracts.adapter_protocol import SubjectScope


def _control(pk):
    item=client('dynamodb').get_item(TableName=os.environ['CONTROL_TABLE'],Key={'pk':{'S':pk}},ConsistentRead=True).get('Item')
    return json.loads(item['value']['S']) if item else None


def _target(event):
    if os.environ.get('WORKER_MODE')=='candidate':
        candidate=identifier(event['candidate_id'])
        record=_control('CANDIDATE#'+candidate)
        if not record or record.get('candidate_id')!=candidate or record.get('status') not in {'ISOLATED','VALIDATED','RELEASED'}:
            raise ValueError('Unknown trusted candidate binding')
        return record['endpoint'],'rgx_recovery'
    active=_control('ACTIVE')
    return (active['endpoint'] if active else os.environ['LIVE_DB_HOST']),'rgx_app'


def _dsn(host,user,password=None):
    if not host.endswith('.rds.amazonaws.com'):
        raise ValueError('Database endpoint is not an RDS endpoint')
    if password is None:
        password=client('rds').generate_db_auth_token(DBHostname=host,Port=5432,DBUsername=user)
    return make_conninfo(host=host,port=5432,dbname='rgx',user=user,password=password,
                         sslmode='verify-full',sslrootcert=str(Path(__file__).parent/'certs'/'rds-bundle.pem'),
                         connect_timeout=8)


def bootstrap(event,context):
    """Separate bootstrap role, static live endpoint, master secret never returned."""
    if event!={'action':'bootstrap'}:
        raise ValueError('Invalid bootstrap request')
    secret=json.loads(client('secretsmanager').get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])['SecretString'])
    dsn=_dsn(os.environ['LIVE_DB_HOST'],secret['username'],secret['password'])
    migrate(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute("DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='rgx_app') THEN CREATE ROLE rgx_app LOGIN; END IF; IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='rgx_recovery') THEN CREATE ROLE rgx_recovery LOGIN; END IF; END $$")
        conn.execute('GRANT rds_iam TO rgx_app,rgx_recovery')
        conn.execute('GRANT USAGE ON SCHEMA public TO rgx_app,rgx_recovery')
        conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO rgx_app,rgx_recovery')
    return success({'migrated':True,'iam_database_users':True})



def immutable_seed_object(s3,bucket,key,content):
    # Missing keys can return 403 to a principal without ListBucket. Create
    # conditionally first; inspect metadata only for an existing immutable key.
    try:
        result=s3.put_object(Bucket=bucket,Key=key,Body=content,
                    Metadata={'content-sha256':sha256(content).hexdigest()},IfNoneMatch='*')
        return result['VersionId']
    except ClientError as error:
        if error.response['Error']['Code'] not in {'412','PreconditionFailed'}:
            raise
    prior=s3.head_object(Bucket=bucket,Key=key)
    if prior.get('Metadata',{}).get('content-sha256')!=sha256(content).hexdigest():
        raise Conflict('Synthetic object operation has different content')
    return prior['VersionId']


def handler(event,context):
    action=event.get('action')
    allowed={'seed','profile','upsert','remediate','verify','reconcile','oracle','search'}
    if os.environ.get('WORKER_MODE')=='candidate':
        allowed={'profile','remediate','verify','oracle','search'}
    if action not in allowed:
        raise ValueError('Unknown data action')
    if action=='oracle' and os.environ.get('WORKER_MODE')!='candidate':
        raise ValueError('Raw oracle is confined to the isolated candidate worker')
    host,user=_target(event)
    dsn=_dsn(host,user)
    auth=authority()
    store=PostgresAdapter(dsn,auth)
    objects=AwsS3Adapter(client('s3'),os.environ['DOCUMENT_BUCKET'],auth,allow_mutation=True)
    index=PostgresSearchIndex(dsn,auth,objects)
    if action=='search':
        return success([asdict(hit) for hit in index.search(identifier(event['tenant_id']),event.get('query','')[:256])])
    scope=SubjectScope(**event['scope'])
    identifier(scope.tenant_id); identifier(scope.subject_id)
    if type(scope.subject_generation) is not int or scope.subject_generation<1:
        raise ValueError('Invalid generation')
    if action=='profile':
        return success(store.public_profile(scope))
    if action=='upsert':
        return success(store.guarded_upsert_profile(scope,operation_id=identifier(event['operation_id']),
                       source_version=event['source_version'],display_name=event['display_name'][:256]))
    if action=='seed':
        request=identifier(event['request_id'])
        try:
            auth.state(scope)
        except NotFound:
            auth.register_subject(scope)
        if not auth.may_read(scope):
            raise AdmissionClosed('Synthetic generation is closed')
        store.guarded_upsert_profile(scope,operation_id='profile_'+request,source_version=1,display_name='Synthetic Cloud Profile')
        key=objects.prefix(scope)+'document_'+request
        content=b'Synthetic RewindGuard cloud index document'
        permit=auth.acquire_permit(scope,'object_'+request)
        version=immutable_seed_object(objects.client,objects.bucket,key,content)
        auth.close_permit(permit.permit_id,outcome='COMMITTED',journal_confirmed=True)
        index.index_object(scope,key=key,version_id=version,document_id='doc_'+request,
                           source_version=1,operation_id='index_'+request)
        return success({'seeded':True,'profile':store.public_profile(scope),
                        'index_hits':len(index.search(scope.tenant_id,'RewindGuard'))})
    if action=='oracle':
        with psycopg.connect(dsn) as conn:
            counts={table:conn.execute('SELECT count(*) FROM '+table+' WHERE tenant_id=%s AND subject_id=%s AND generation=%s',
                    (scope.tenant_id,scope.subject_id,scope.subject_generation)).fetchone()[0]
                    for table in ('profiles','documents','index_chunks')}
        return success({'raw_counts':counts,'guarded_profile':store.public_profile(scope)})
    adapter={'rds':store,'index':index}[event['adapter']]
    if action=='reconcile':
        for permit in auth.unresolved_permits(scope):
            try: store.reconcile_permit(permit.permit_id,permit.operation_id)
            except Exception: pass
        return success({'reconciled':True})
    if action=='remediate':
        adapter.remediate(scope,event['decision_id'])
        return success({'remediated':True})
    return success(adapter.verify(scope,event['authority_epoch']))
