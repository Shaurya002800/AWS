"""API Gateway JWT-authenticated application; no demo shared-secret fallback."""
import base64
import json
import os
from pathlib import Path
from uuid import uuid4
from backend.cloud.common import client,identifier,invoke,canonical
from backend.cloud.runtime import erase
from backend.cloud.recovery import get_record,put_record,active_db,db
from contracts.adapter_protocol import SubjectScope
from backend.eraselock.domain import EraseLockError


def response(status,body,content_type='application/json'):
    return {'statusCode':status,'headers':{'content-type':content_type,'cache-control':'no-store',
            'x-content-type-options':'nosniff','referrer-policy':'no-referrer',
            'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://*.amazoncognito.com; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"},
            'body':json.dumps(body) if content_type=='application/json' else body}


def principal(event):
    # API Gateway verifies issuer, audience, expiration and signature. Lambda
    # permission is restricted to this API; direct IAM Invoke is privileged.
    claims=event.get('requestContext',{}).get('authorizer',{}).get('jwt',{}).get('claims',{})
    sub=claims.get('sub')
    tenant=claims.get('custom:tenant_id')
    if not sub or not tenant:
        raise PermissionError('Verified project identity required')
    groups=claims.get('cognito:groups','')
    if isinstance(groups,str): groups=groups.replace('[','').replace(']','').replace('"','').split(',')
    return {'tenant_id':identifier(tenant),'subject_id':'u_'+sub.replace('-',''),
            'operator':'operators' in [g.strip() for g in groups]}


def body(event):
    text=event.get('body') or '{}'
    if event.get('isBase64Encoded'): text=base64.b64decode(text).decode()
    if len(text)>16384: raise ValueError('Request is too large')
    value=json.loads(text)
    if not isinstance(value,dict): raise ValueError('JSON object required')
    return value


def scope_for(user,payload):
    subject=payload.get('subject_id',user['subject_id'])
    if subject!=user['subject_id'] and not user['operator']:
        raise PermissionError('Cannot access another subject')
    if payload.get('tenant_id',user['tenant_id'])!=user['tenant_id']:
        raise PermissionError('Tenant mismatch')
    generation=payload.get('subject_generation',1)
    if type(generation) is not int or not 1<=generation<=1000000: raise ValueError('Invalid generation')
    return SubjectScope(user['tenant_id'],identifier(subject),generation)


def handler(event,context):
    method=event.get('requestContext',{}).get('http',{}).get('method','')
    path=event.get('rawPath','/')
    try:
        if method=='GET' and path=='/health':
            return response(200,{'status':'ok','mode':'aws-hosted','region':os.environ['AWS_REGION']})
        if method=='GET' and path in {'/','/ui','/callback'}:
            return response(200,(Path(__file__).parents[2]/'frontend'/'cloud.html').read_text(), 'text/html; charset=utf-8')
        if method=='GET' and path=='/app.js':
            config={'clientId':os.environ['USER_POOL_CLIENT'],'authDomain':os.environ['AUTH_DOMAIN'],
                    'redirectUri':os.environ['API_URL']+'/callback'}
            script='const AUTH_CONFIG='+json.dumps(config)+';\n'+(Path(__file__).parents[2]/'frontend'/'cloud.js').read_text()
            return response(200,script,'application/javascript; charset=utf-8')
        if method=='GET' and path=='/style.css':
            return response(200,(Path(__file__).parents[2]/'frontend'/'style.css').read_text(),'text/css; charset=utf-8')
        user=principal(event)
        payload=body(event) if method=='POST' else dict(event.get('queryStringParameters') or {})
        if method=='GET' and path=='/v1/me':
            return response(200,user)
        if method=='POST' and path=='/v1/lab/seed':
            if not user['operator']: raise PermissionError('Operator access required')
            scope=scope_for(user,payload)
            value=invoke(os.environ['DATA_FUNCTION'],{'action':'seed','scope':scope.__dict__ if hasattr(scope,'__dict__') else
                        {'tenant_id':scope.tenant_id,'subject_id':scope.subject_id,'subject_generation':scope.subject_generation},
                        'request_id':identifier(payload.get('request_id','seed_'+uuid4().hex))})
            return response(200,value)
        if method=='POST' and path=='/v1/erase-requests':
            scope=scope_for(user,payload)
            return response(200,erase(scope,identifier(payload.get('request_id','erase_'+uuid4().hex))))
        if method=='GET' and path=='/v1/profile':
            from dataclasses import asdict
            return response(200,invoke(os.environ['DATA_FUNCTION'],{'action':'profile','scope':asdict(scope_for(user,payload))}))
        if method=='POST' and path=='/v1/lab/recovery':
            if not user['operator']: raise PermissionError('Operator access required')
            from dataclasses import asdict
            scope=scope_for(user,payload)
            run=uuid4().hex
            item={'run_id':run,'tenant_id':user['tenant_id'],'scope':asdict(scope)}
            execution=client('stepfunctions').start_execution(stateMachineArn=os.environ['RECOVERY_STATE_MACHINE'],name='rgx-'+run,input=json.dumps(item))
            put_record('RUN#'+run,{'tenant_id':user['tenant_id'],'subject_id':user['subject_id'],'execution_arn':execution['executionArn']})
            return response(202,{'run_id':run,'status':'RUNNING'})
        if method=='GET' and path.startswith('/v1/runs/'):
            run=identifier(path.rsplit('/',1)[-1])
            record=get_record('RUN#'+run)
            if not record or record['tenant_id']!=user['tenant_id'] or record['subject_id']!=user['subject_id']:
                raise PermissionError('Run is not owned by this identity')
            execution=client('stepfunctions').describe_execution(executionArn=record['execution_arn'])
            result={'run_id':run,'status':execution['status']}
            if execution.get('output'):
                output=json.loads(execution['output'])
                result['result']={k:v for k,v in output.items() if k in
                     {'erasure_state','raw_before','guarded_before','raw_after','evidence_signed','release','finished_at'}}
            if execution['status'] in {'FAILED','TIMED_OUT','ABORTED'}: result['detail']='Workflow held; inspect project monitoring.'
            return response(200,result)
        return response(404,{'detail':'Not found'})
    except PermissionError:
        return response(403,{'detail':'Access denied'})
    except (ValueError,KeyError,TypeError):
        return response(422,{'detail':'Invalid request'})
    except EraseLockError as error:
        return response(409,{'code':error.code,'detail':str(error)})
    except Exception as error:
        print(json.dumps({'event':'request_held','type':type(error).__name__}))
        return response(503,{'detail':'Operation held; retry after checking project health.'})
