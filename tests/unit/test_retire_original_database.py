import json
from unittest.mock import MagicMock
import pytest
from scripts import retire_original_database as retire


def services(monkeypatch,active='rgx-candidate-approved'):
    cf=MagicMock(); ddb=MagicMock(); rds=MagicMock()
    monkeypatch.setattr(retire,'free_guard',lambda _:None)
    monkeypatch.setattr(retire,'sdk',lambda sess,name:{'cloudformation':cf,'dynamodb':ddb,'rds':rds}[name])
    monkeypatch.setattr(retire,'stack_outputs',lambda *args:({'StackStatus':'UPDATE_COMPLETE','Parameters':[{'ParameterKey':'EnableBootstrap','ParameterValue':'false'}]},{'ControlTableName':'control'}))
    ddb.get_item.return_value={'Item':{'value':{'S':json.dumps({'db_id':active})}}}
    rds.describe_db_instances.return_value={'DBInstances':[{'DBInstanceArn':'owned','PubliclyAccessible':False,'StorageEncrypted':True}]}
    rds.list_tags_for_resource.return_value={'TagList':[{'Key':'Project','Value':'rewindguard-x'}]}
    return cf,rds


def test_retirement_defaults_to_preview_without_deletion(monkeypatch):
    cf,rds=services(monkeypatch)
    result=retire.retire_original(None)
    assert result['database_to_delete']=='rgx-live'
    assert result['preserve_published_database']=='rgx-candidate-approved'
    cf.update_stack.assert_not_called()
    rds.delete_db_instance.assert_not_called()


def test_retirement_rejects_original_serving_database(monkeypatch):
    cf,rds=services(monkeypatch,active='rgx-live')
    with pytest.raises(RuntimeError,match='cannot be retired'):
        retire.retire_original(None,execute=True)
    cf.update_stack.assert_not_called()
    rds.describe_db_instances.assert_not_called()
