"""Prepare an owner review login without sending invitations or printing a password."""
import argparse
from datetime import datetime,timedelta,timezone
from pathlib import Path
import secrets
from botocore.exceptions import ClientError
from backend.cloud.common import identifier
from scripts.deploy_application import ROOT,session,sdk,stack_outputs,STACK,free_guard


def prepare_review_login(sess,username='review-owner',credentials_path=None):
    identifier(username)
    free_guard(sess)
    _,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    cog=sdk(sess,'cognito-idp')
    try:
        user=cog.admin_get_user(UserPoolId=outputs['UserPoolId'],Username=username)
    except ClientError as error:
        if error.response['Error']['Code']!='UserNotFoundException': raise
    else:
        result={'username':username,'existing_login_preserved':True,'status':user['UserStatus'],'ui_url':outputs['ApiUrl']+'/ui'}
        print(result,flush=True)
        return result
    target=Path(credentials_path) if credentials_path else ROOT/'evidence'/'private'/'review-login.txt'
    if target.exists(): raise RuntimeError('Existing private login file must be inspected; it will not be overwritten')
    password='Aa7!'+secrets.token_urlsafe(24)
    created=False
    try:
        cog.admin_create_user(UserPoolId=outputs['UserPoolId'],Username=username,TemporaryPassword=password,MessageAction='SUPPRESS',UserAttributes=[{'Name':'custom:tenant_id','Value':'synthetic'}])
        created=True
        cog.admin_add_user_to_group(UserPoolId=outputs['UserPoolId'],Username=username,GroupName='operators')
        challenge=cog.initiate_auth(ClientId=outputs['UserPoolClientId'],AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':username,'PASSWORD':password})
        if challenge.get('ChallengeName')!='NEW_PASSWORD_REQUIRED': raise RuntimeError('First-login challenge was not verified')
        target.parent.mkdir(parents=True,exist_ok=True)
        expires=datetime.now(timezone.utc)+timedelta(days=1)
        text='PRIVATE FIRST-LOGIN DETAILS — do not commit or share\n\nDemo: '+outputs['ApiUrl']+'/ui\nUsername: '+username+'\nTemporary password: '+password+'\n\nSign in and choose your own permanent password when Cognito asks.\nUse at least 16 characters with uppercase, lowercase, a number and a symbol.\nThis temporary password expires approximately '+expires.isoformat()+'.\nNo invitation or email was sent. This account is for the owner, not public reviewers.\n'
        target.write_text(text,encoding='utf-8')
    except Exception:
        if created: cog.admin_delete_user(UserPoolId=outputs['UserPoolId'],Username=username)
        raise
    result={'username':username,'first_login_challenge_verified':True,'password_change_required':True,'private_credentials_file':str(target),'ui_url':outputs['ApiUrl']+'/ui'}
    print(result,flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--username',default='review-owner')
    args=parser.parse_args()
    prepare_review_login(session(),args.username)
