"""Owner-run creation of a Cognito lab login. Password is entered privately."""
import argparse
import getpass
from scripts.deploy_application import session,sdk,stack_outputs,STACK,free_guard


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--username',required=True)
    args=parser.parse_args()
    password=getpass.getpass('Choose a project password (16+ characters, upper/lowercase, number and symbol): ')
    confirm=getpass.getpass('Confirm project password: ')
    if password!=confirm: raise SystemExit('Passwords do not match')
    if len(password)<16: raise SystemExit('Password is too short')
    sess=session(); free_guard(sess)
    _,outputs=stack_outputs(sdk(sess,'cloudformation'),STACK)
    cog=sdk(sess,'cognito-idp')
    cog.admin_create_user(UserPoolId=outputs['UserPoolId'],Username=args.username,MessageAction='SUPPRESS',
                          UserAttributes=[{'Name':'custom:tenant_id','Value':'synthetic'}])
    cog.admin_set_user_password(UserPoolId=outputs['UserPoolId'],Username=args.username,Password=password,Permanent=True)
    cog.admin_add_user_to_group(UserPoolId=outputs['UserPoolId'],Username=args.username,GroupName='operators')
    print('Project operator created. Sign in at '+outputs['ApiUrl']+'/ui')
