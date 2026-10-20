from unittest.mock import MagicMock
import json
import pytest
from botocore.exceptions import ClientError
from scripts import prepare_review_login as login
from scripts import start_cloud_demo as startup


OUTPUTS={'UserPoolId':'pool','UserPoolClientId':'client','ApiUrl':'https://demo.example','ControlTableName':'control','DatabaseId':'rgx-live'}


def login_services(monkeypatch):
    cog=MagicMock()
    monkeypatch.setattr(login,'free_guard',lambda _:None)
    monkeypatch.setattr(login,'sdk',lambda sess,name:cog)
    monkeypatch.setattr(login,'stack_outputs',lambda *args:({},OUTPUTS))
    return cog


def test_existing_review_login_is_never_reset(monkeypatch,tmp_path):
    cog=login_services(monkeypatch)
    cog.admin_get_user.return_value={'UserStatus':'CONFIRMED'}
    target=tmp_path/'login.txt'
    outcome=login.prepare_review_login(None,credentials_path=target)
    assert outcome['existing_login_preserved'] is True
    cog.admin_create_user.assert_not_called()
    cog.admin_set_user_password.assert_not_called()
    cog.admin_add_user_to_group.assert_not_called()
    assert not target.exists()


def test_review_login_forces_owner_password_change_and_keeps_secret_private(monkeypatch,tmp_path,capsys):
    cog=login_services(monkeypatch)
    cog.admin_get_user.side_effect=ClientError({'Error':{'Code':'UserNotFoundException'}},'AdminGetUser')
    cog.initiate_auth.return_value={'ChallengeName':'NEW_PASSWORD_REQUIRED'}
    target=tmp_path/'login.txt'
    outcome=login.prepare_review_login(None,credentials_path=target)
    arguments=cog.admin_create_user.call_args.kwargs
    assert arguments['MessageAction']=='SUPPRESS'
    assert arguments['UserAttributes']==[{'Name':'custom:tenant_id','Value':'synthetic'}]
    assert outcome['password_change_required'] is True
    assert arguments['TemporaryPassword'] in target.read_text(encoding='utf-8')
    assert arguments['TemporaryPassword'] not in capsys.readouterr().out
    cog.admin_set_user_password.assert_not_called()


def test_failed_new_login_setup_removes_only_the_new_account(monkeypatch,tmp_path):
    cog=login_services(monkeypatch)
    cog.admin_get_user.side_effect=ClientError({'Error':{'Code':'UserNotFoundException'}},'AdminGetUser')
    cog.initiate_auth.return_value={'ChallengeName':'UNEXPECTED'}
    target=tmp_path/'login.txt'
    with pytest.raises(RuntimeError): login.prepare_review_login(None,credentials_path=target)
    cog.admin_delete_user.assert_called_once_with(UserPoolId='pool',Username='review-owner')
    assert not target.exists()


@pytest.mark.parametrize('minutes',[0,121,True])
def test_review_window_is_validated_before_cloud_calls(monkeypatch,minutes):
    service=MagicMock()
    monkeypatch.setattr(startup,'sdk',service)
    with pytest.raises(ValueError): startup.start_demo(None,minutes)
    service.assert_not_called()


def test_startup_resumes_only_approved_database_and_sets_short_expiry(monkeypatch):
    cf=MagicMock(); ddb=MagicMock(); rds=MagicMock()
    monkeypatch.setattr(startup,'sdk',lambda sess,name:{'cloudformation':cf,'dynamodb':ddb,'rds':rds}[name])
    monkeypatch.setattr(startup,'free_guard',lambda _:None)
    monkeypatch.setattr(startup,'stack_outputs',lambda *args:({},OUTPUTS))
    monkeypatch.setattr(startup.time,'time',lambda:1000)
    ddb.get_item.return_value={'Item':{'value':{'S':json.dumps({'db_id':'rgx-candidate-approved'})}}}
    rds.describe_db_instances.return_value={'DBInstances':[{'DBInstanceArn':'owned-arn','DBInstanceStatus':'stopped','PubliclyAccessible':False,'StorageEncrypted':True}]}
    rds.list_tags_for_resource.return_value={'TagList':[{'Key':'Project','Value':'rewindguard-x'}]}
    result=startup.start_demo(None,30)
    rds.start_db_instance.assert_called_once_with(DBInstanceIdentifier='rgx-candidate-approved')
    rds.get_waiter.return_value.wait.assert_called_once()
    rds.add_tags_to_resource.assert_called_once_with(ResourceName='owned-arn',Tags=[{'Key':'ExpiresAt','Value':'2800'}])
    assert result['review_window_minutes']==30


@pytest.mark.parametrize('allow_running',[False,True])
def test_checkpoint_never_rolls_back_code_or_stops_review_database(monkeypatch,tmp_path,allow_running):
    from scripts import verify_cloud_checkpoint as checkpoint
    monkeypatch.chdir(tmp_path)
    private=tmp_path/'evidence'/'private'
    private.mkdir(parents=True)
    (private/'cloud-evaluation.json').write_text(json.dumps({'all_checks_passed':True}))
    (private/'code-patch.json').write_text('stale invalid patch must not be read')
    cf=MagicMock(); rds=MagicMock()
    monkeypatch.setattr(checkpoint,'free_guard',lambda _:None)
    monkeypatch.setattr(checkpoint,'sdk',lambda sess,name:cf if name=='cloudformation' else rds)
    monkeypatch.setattr(checkpoint,'stack_outputs',lambda *args:({'StackStatus':'UPDATE_COMPLETE'},{'BootstrapFunctionName':'disabled'}))
    rds.describe_db_instances.return_value={'DBInstances':[{'DBInstanceIdentifier':'rgx-candidate-approved','DBInstanceArn':'owned','DBInstanceStatus':'available'}]}
    rds.list_tags_for_resource.return_value={'TagList':[{'Key':'Project','Value':'rewindguard-x'}]}
    rds.get_paginator.return_value.paginate.return_value=[{'DBSnapshots':[]}]
    if allow_running:
        assert checkpoint.verify_checkpoint(None,allow_running=True)['all_databases_stopped'] is False
    else:
        with pytest.raises(RuntimeError,match='not in standby'): checkpoint.verify_checkpoint(None)
    cf.update_stack.assert_not_called()
    rds.stop_db_instance.assert_not_called()
