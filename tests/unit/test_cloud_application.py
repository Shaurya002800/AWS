import copy
from dataclasses import replace
from hashlib import sha256
from unittest.mock import Mock,patch
import pytest
from infra.build_application import template
from backend.cloud.http import principal,scope_for,handler
from backend.cloud.signing import KmsEvidenceSigner
from backend.eraselock.evidence import certificate_for


class FakeKms:
    def get_public_key(self,**kwargs): return {'KeyId':'trusted-key','PublicKey':b'test-public-key'}
    def sign(self,**kwargs): return {'Signature':sha256(b'fake-key'+kwargs['Message']).digest()}
    def verify(self,**kwargs): return {'SignatureValid':kwargs['Signature']==sha256(b'fake-key'+kwargs['Message']).digest()}


def test_kms_envelope_authenticates_certificate_and_key_identity():
    signer=KmsEvidenceSigner(FakeKms(),'trusted-key')
    certificate=certificate_for('synthetic/test/g1',2,'PASS',[])
    envelope=signer.sign(certificate)
    assert signer.verify(envelope)
    assert envelope['certificate']['signature_status']=='KMS_ECDSA_SHA_256'
    forged=copy.deepcopy(envelope)
    forged['certificate']['authority_epoch']=999
    assert not signer.verify(forged)
    forged=copy.deepcopy(envelope)
    forged['key_id']='untrusted-key'
    assert not signer.verify(forged)
    with pytest.raises(ValueError): signer.sign(replace(certificate,decision='HOLD'))


def test_cloud_auth_has_no_identity_header_fallback():
    with pytest.raises(PermissionError): principal({'headers':{'X-RGX-Tenant':'synthetic','X-RGX-Subject':'test'}})
    event={'requestContext':{'authorizer':{'jwt':{'claims':{'sub':'abc-123','custom:tenant_id':'synthetic','cognito:groups':'["operators"]'}}}}}
    assert principal(event)=={'tenant_id':'synthetic','subject_id':'u_abc123','operator':True}


def test_scope_cannot_cross_tenant_or_impersonate_subject():
    user={'tenant_id':'synthetic','subject_id':'self','operator':False}
    for body in ({'tenant_id':'other'},{'subject_id':'other'},{'subject_generation':True},{'subject_id':'../escape'}):
        with pytest.raises((PermissionError,ValueError)): scope_for(user,body)


def test_unauthenticated_mutation_fails_without_aws_calls():
    event={'rawPath':'/v1/erase-requests','requestContext':{'http':{'method':'POST'}},'body':'{}'}
    with patch('backend.cloud.http.client') as sdk:
        assert handler(event,None)['statusCode']==403
        sdk.assert_not_called()


def test_rds_and_candidate_networks_are_private_and_distinct():
    resources=template()['Resources']
    database=resources['Database']['Properties']
    assert database['PubliclyAccessible'] is False
    assert database['StorageEncrypted'] is True
    assert database['EnableIAMDatabaseAuthentication'] is True
    assert not any(x['Type'] in {'AWS::EC2::NatGateway','AWS::EC2::InternetGateway'} for x in resources.values())
    assert resources['CandidateDbSg']['Properties']['SecurityGroupIngress']==[
        {'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'SourceSecurityGroupId':{'Ref':'RecoverySg'}}]
    assert resources['DataFunction']['Properties']['VpcConfig']!=resources['CandidateFunction']['Properties']['VpcConfig']


def test_every_mutating_public_route_requires_cognito_jwt():
    resources=template()['Resources']
    routes=[r['Properties'] for r in resources.values() if r['Type']=='AWS::ApiGatewayV2::Route']
    assert next(r for r in routes if r['RouteKey']=='$default')['AuthorizationType']=='JWT'
    assert all(r['RouteKey'].startswith('GET ') for r in routes if r['AuthorizationType']=='NONE')
    assert resources['UserPoolClient']['Properties']['GenerateSecret'] is False
    assert resources['UserPoolClient']['Properties']['AllowedOAuthFlows']==['code']
    assert 'custom:tenant_id' not in resources['UserPoolClient']['Properties']['WriteAttributes']


def test_serving_roles_do_not_receive_master_secret():
    resources=template()['Resources']
    for name in ('DataFunction','CandidateFunction','HttpFunction','RecoveryFunction'):
        assert 'DB_SECRET_ARN' not in resources[name]['Properties']['Environment']['Variables']
    assert resources['BootstrapFunction']['Condition']=='BootstrapEnabled'
    assert resources['SecretEndpoint']['Condition']=='BootstrapEnabled'


def test_workflow_waits_checks_and_holds_on_task_failure():
    definition=template()['Resources']['RecoveryWorkflow']['Properties']['Definition']
    assert definition['TimeoutSeconds']==3600
    states=definition['States']
    for name in ('Snapshot','Erase','Restore','Validate','Release'):
        assert states[name]['Catch'][0]['Next']=='Hold'
    assert states['SnapshotReady']['Default']=='WaitSnapshot'
    assert states['RestoreReady']['Default']=='WaitRestore'


def test_idle_cleanup_preserves_active_candidate(monkeypatch):
    from backend.cloud import cleanup
    from unittest.mock import MagicMock
    rds=MagicMock()
    active='rgx-candidate-active'
    unused='rgx-candidate-unused'
    rows=[{'DBInstanceIdentifier':name,'DBInstanceArn':name,'DBInstanceStatus':'available'} for name in (active,unused)]
    def pages(kind):
        paginator=MagicMock()
        paginator.paginate.return_value=[{'DBInstances':rows}] if kind=='describe_db_instances' else [{'DBSnapshots':[]}]
        return paginator
    rds.get_paginator.side_effect=pages
    rds.list_tags_for_resource.return_value={'TagList':[{'Key':'Project','Value':'rewindguard-x'},{'Key':'Disposable','Value':'true'},{'Key':'ExpiresAt','Value':'0'}]}
    monkeypatch.setattr(cleanup,'client',lambda _:rds)
    monkeypatch.setattr(cleanup,'get_record',lambda _: {'db_id':active})
    cleanup.handler({},None)
    rds.stop_db_instance.assert_called_once_with(DBInstanceIdentifier=active)
    rds.delete_db_instance.assert_called_once_with(DBInstanceIdentifier=unused,SkipFinalSnapshot=True,DeleteAutomatedBackups=True)


def test_free_plan_guard_accepts_small_positive_balance_and_rejects_paid(monkeypatch):
    from scripts import deploy_application as deploy
    from unittest.mock import MagicMock
    service=MagicMock()
    monkeypatch.setattr(deploy,'sdk',lambda *args:service)
    state={'accountPlanType':'FREE','accountPlanStatus':'ACTIVE','accountPlanRemainingCredits':{'unit':'USD','amount':0.01}}
    service.get_account_plan_state.return_value=state
    assert deploy.free_guard(None)==state
    for invalid in ({**state,'accountPlanType':'PAID'}, {**state,'accountPlanStatus':'EXPIRED'}, {**state,'accountPlanRemainingCredits':{'unit':'USD','amount':0}}):
        service.get_account_plan_state.return_value=invalid
        with pytest.raises(RuntimeError): deploy.free_guard(None)


def test_immutable_seed_create_does_not_head_missing_key():
    from backend.cloud.data import immutable_seed_object
    from unittest.mock import MagicMock
    client=MagicMock()
    client.put_object.return_value={'VersionId':'v1'}
    assert immutable_seed_object(client,'bucket','synthetic/key',b'data')=='v1'
    assert client.put_object.call_args.kwargs['IfNoneMatch']=='*'
    client.head_object.assert_not_called()


def test_immutable_seed_retry_requires_matching_content():
    from backend.cloud.data import immutable_seed_object
    from backend.eraselock.domain import Conflict
    from botocore.exceptions import ClientError
    from hashlib import sha256
    from unittest.mock import MagicMock
    client=MagicMock()
    client.put_object.side_effect=ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
    client.head_object.return_value={'VersionId':'v1','Metadata':{'content-sha256':sha256(b'data').hexdigest()}}
    assert immutable_seed_object(client,'bucket','synthetic/key',b'data')=='v1'
    with pytest.raises(Conflict): immutable_seed_object(client,'bucket','synthetic/key',b'changed')


@pytest.mark.parametrize('failure',[False,True])
def test_cloud_evaluation_stops_databases_on_success_and_failure(failure):
    from scripts import evaluate_application as evaluation
    session=object()
    result={'all_checks_passed':True}
    with patch.object(evaluation,'evaluate',return_value=result,side_effect=RuntimeError('held') if failure else None), patch('scripts.stop_cloud_demo.stop') as stop:
        if failure:
            with pytest.raises(RuntimeError,match='held'): evaluation.run_evaluation(session)
        else:
            assert evaluation.run_evaluation(session)==result
        stop.assert_called_once_with(session)


def test_cloud_page_serves_its_own_stylesheet_without_changing_local_ui():
    page=handler({'rawPath':'/ui','requestContext':{'http':{'method':'GET'}}},None)
    style=handler({'rawPath':'/style.css','requestContext':{'http':{'method':'GET'}}},None)
    assert page['statusCode']==200 and 'class="notebook"' in page['body']
    assert style['statusCode']==200 and '--paper:' in style['body']
    assert style['headers']['content-type']=='text/css; charset=utf-8'
    assert "script-src 'self'" in page['headers']['content-security-policy']


def test_capacity_guard_counts_stopped_instances_and_never_erases(monkeypatch):
    from backend.cloud import recovery
    from unittest.mock import MagicMock
    rds=MagicMock()
    rds.get_paginator.return_value.paginate.return_value=[{'DBInstances':[{'DBInstanceStatus':'available'}]},{'DBInstances':[{'DBInstanceStatus':'stopped'}]}]
    monkeypatch.setattr(recovery,'client',lambda _:rds)
    with patch.object(recovery,'active_db') as active:
        with pytest.raises(recovery.RecoveryCapacityUnavailable):
            recovery.handler({'action':'snapshot','run_id':'test','tenant_id':'synthetic','scope':{'tenant_id':'synthetic','subject_id':'demo_test','subject_generation':1}},None)
        active.assert_not_called()
    rds.create_db_snapshot.assert_not_called()
    rds.delete_db_instance.assert_not_called()


def test_capacity_guard_allows_one_existing_database():
    from backend.cloud.recovery import ensure_restore_capacity
    from unittest.mock import MagicMock
    rds=MagicMock()
    rds.get_paginator.return_value.paginate.return_value=[{'DBInstances':[{}]}]
    ensure_restore_capacity(rds)


def test_authenticated_failure_exposes_safe_capacity_reason_only():
    event={'rawPath':'/v1/runs/test','requestContext':{'http':{'method':'GET'}}}
    user={'tenant_id':'synthetic','subject_id':'self','operator':True}
    with patch('backend.cloud.http.principal',return_value=user),patch('backend.cloud.http.get_record',return_value={'tenant_id':'synthetic','subject_id':'self','execution_arn':'test'}),patch('backend.cloud.http.client') as sdk:
        sdk.return_value.describe_execution.return_value={'status':'FAILED','cause':'RecoveryCapacityUnavailable'}
        import json
        value=json.loads(handler(event,None)['body'])
        assert value['code']=='RESTORE_CAPACITY_REQUIRED'
        assert 'do not upgrade' in value['detail']
        sdk.return_value.get_execution_history.assert_not_called()


def test_original_database_retirement_is_opt_in_and_failure_reason_is_preserved():
    generated=template()
    assert generated['Parameters']['RetainOriginalDatabase']['Default']=='true'
    assert generated['Resources']['Database']['Condition']=='SourceDatabaseEnabled'
    assert generated['Outputs']['DatabaseId']['Value']['Fn::If'][2]=='retired'
    states=generated['Resources']['RecoveryWorkflow']['Properties']['Definition']['States']
    assert states['Snapshot']['Catch'][0]['ResultPath']=='$.failure'
    assert states['Hold']['CausePath']=='$.failure.Error'


def test_cleanup_reclaims_superseded_released_candidate_before_expiry(monkeypatch):
    from backend.cloud import cleanup
    from unittest.mock import MagicMock
    rds=MagicMock()
    old='rgx-candidate-old'; current='rgx-candidate-current'; pending='rgx-candidate-pending'
    rows=[{'DBInstanceIdentifier':name,'DBInstanceArn':name,'DBInstanceStatus':'stopped' if name==old else 'available'} for name in (old,current,pending)]
    def pages(kind):
        p=MagicMock()
        p.paginate.return_value=[{'DBInstances':rows}] if kind=='describe_db_instances' else [{'DBSnapshots':[]}]
        return p
    rds.get_paginator.side_effect=pages
    rds.list_tags_for_resource.return_value={'TagList':[{'Key':'Project','Value':'rewindguard-x'},{'Key':'Disposable','Value':'true'},{'Key':'ExpiresAt','Value':'9999999999'}]}
    monkeypatch.setattr(cleanup,'client',lambda _:rds)
    monkeypatch.setattr(cleanup,'get_record',lambda key: {'db_id':current} if key=='ACTIVE' else {'status':'RELEASED' if key.endswith(old) else 'ISOLATED'})
    result=cleanup.handler({},None)
    rds.delete_db_instance.assert_called_once_with(DBInstanceIdentifier=old,SkipFinalSnapshot=True,DeleteAutomatedBackups=True)
    rds.stop_db_instance.assert_not_called()
    assert result['actions']==[{'kind':'superseded_candidate_deleted'}]


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_release_requests_cleanup_without_undoing_verified_publication(monkeypatch,cleanup_fails):
    from backend.cloud import recovery
    from unittest.mock import MagicMock
    services={name:MagicMock() for name in ('rds','s3','lambda')}
    monkeypatch.setattr(recovery,'client',lambda name:services[name])
    monkeypatch.setenv('DOCUMENT_BUCKET','bucket')
    monkeypatch.setenv('CLEANUP_FUNCTION','cleanup')
    monkeypatch.setattr(recovery,'publish',lambda *args:{'state':'RELEASED'})
    if cleanup_fails:services['lambda'].invoke.side_effect=RuntimeError('retry cleanup later')
    value=recovery.handler({'action':'release','run_id':'test','tenant_id':'synthetic','scope_digest':'digest','scope':{'tenant_id':'synthetic','subject_id':'demo_test','subject_generation':1}},None)
    assert value['release']['state']=='RELEASED'
    services['lambda'].invoke.assert_called_once_with(FunctionName='cleanup',InvocationType='Event',Payload=b'{}')


def test_redeploy_preserves_owner_retirement_and_disabled_bootstrap():
    from scripts.deploy_application import preserve_lifecycle_parameters
    parameters={'CodeKey':'new-code','EnableBootstrap':'true','RetainOriginalDatabase':'true'}
    existing={'Parameters':[{'ParameterKey':'EnableBootstrap','ParameterValue':'false'},{'ParameterKey':'RetainOriginalDatabase','ParameterValue':'false'}]}
    updated=preserve_lifecycle_parameters(parameters,existing)
    assert updated=={'CodeKey':'new-code','EnableBootstrap':'false','RetainOriginalDatabase':'false'}


def test_cleanup_role_can_read_candidate_release_records_without_writing_them():
    statements=template()['Resources']['CleanupRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    control=next(s for s in statements if s.get('Condition',{}).get('ForAllValues:StringLike',{}).get('dynamodb:LeadingKeys')==['ACTIVE','CANDIDATE#*'])
    assert control['Action']==['dynamodb:GetItem']
