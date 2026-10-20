from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from backend.aws_runtime import aws_runtime, check_free_plan
from backend.aws_runtime import LocalQueueProfiles
from backend.aws_runtime import AwsRuntime
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import AdmissionClosed
from backend.local_runtime import temporary_local_directory
from contracts.adapter_protocol import SubjectScope


def test_paid_plan_blocks_before_resource_access():
    session = Mock()
    session.client.return_value.get_account_plan_state.return_value = {
        'accountPlanType':'PAID', 'accountPlanStatus':'ACTIVE',
        'accountPlanRemainingCredits':{'amount':100,'unit':'USD'}}
    with pytest.raises(RuntimeError, match='Free plan'):
        aws_runtime('unused', b'e' * 32, session=session)
    assert session.client.call_count == 1


@pytest.mark.parametrize('amount',[0,9.99])
def test_low_credit_balance_blocks(amount):
    client=Mock()
    client.get_account_plan_state.return_value={
        'accountPlanType':'FREE','accountPlanStatus':'ACTIVE',
        'accountPlanRemainingCredits':{'amount':amount,'unit':'USD'}}
    with pytest.raises(RuntimeError):
        check_free_plan(client)


def test_other_region_rejected_without_any_aws_call():
    session=Mock()
    with pytest.raises(ValueError,match='ap-southeast-2'):
        aws_runtime('unused',b'e'*32,region='us-east-1',session=session)
    session.client.assert_not_called()


def test_foreign_stack_is_not_connected():
    session=Mock()
    client=session.client.return_value
    client.get_account_plan_state.return_value={
        'accountPlanType':'FREE','accountPlanStatus':'ACTIVE',
        'accountPlanRemainingCredits':{'amount':100,'unit':'USD'}}
    client.describe_stacks.return_value={'Stacks':[{'Tags':[],'StackStatus':'CREATE_COMPLETE'}]}
    with pytest.raises(RuntimeError,match='project-tagged'):
        aws_runtime('unused',b'e'*32,session=session)


def test_queue_records_are_idempotent_and_fenced():
    with temporary_local_directory() as root:
        authority = InMemoryAuthority()
        scope = SubjectScope('synthetic','queue',1)
        authority.register_subject(scope)
        records = LocalQueueProfiles(root/'records.sqlite3',authority,'local-records')
        args=dict(operation_id='same-event',source_version=1,display_name='Synthetic')
        assert not records.guarded_upsert_profile(scope,**args).replayed
        assert records.guarded_upsert_profile(scope,**args).replayed
        authority.close_admission(scope,'erase')
        with pytest.raises(AdmissionClosed):
            records.guarded_upsert_profile(scope,**args)


def test_factory_connects_only_expected_clients():
    session=Mock()
    free=Mock()
    free.get_account_plan_state.return_value={
        'accountPlanType':'FREE','accountPlanStatus':'ACTIVE',
        'accountPlanRemainingCredits':{'amount':100,'unit':'USD'}}
    cf=Mock()
    cf.describe_stacks.return_value={'Stacks':[{
        'StackStatus':'CREATE_COMPLETE','Tags':[{'Key':'Project','Value':'rewindguard-x'}],
        'Outputs':[{'OutputKey':key,'OutputValue':value} for key,value in {
            'AuthorityTableName':'authority','DocumentBucketName':'private-bucket',
            'ProfileEventQueueUrl':'https://sqs.example/source','DeadLetterQueueUrl':'https://sqs.example/dlq'
        }.items()]}]}
    clients={'freetier':free,'cloudformation':cf,'dynamodb':Mock(),'s3':Mock(),'sqs':Mock()}
    session.client.side_effect=lambda name,**kw: clients[name]
    with temporary_local_directory() as root:
        runtime=aws_runtime(root,b'e'*32,session=session)
        assert runtime.objects.bucket=='private-bucket'
        assert runtime.tasks.authority.table_name=='authority'
        assert runtime.worker.store is runtime.records
        assert runtime.tasks.candidate_id is None
    assert {c.args[0] for c in session.client.call_args_list}==set(clients)


def test_sender_returns_the_sqs_message_id_for_matching():
    tasks=Mock()
    tasks.authority.epoch.return_value=0
    sqs=Mock()
    sqs.send_message.return_value={'MessageId':'specific-sqs-message'}
    runtime=AwsRuntime(tasks,Mock(),Mock(),Mock(),sqs,'queue-url')
    assert runtime.send_profile(SubjectScope('synthetic','sender',1),'Synthetic')=='specific-sqs-message'
