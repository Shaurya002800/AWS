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
