"""Generate the complete, private cloud research stack. Never deploys on import."""
import json
from pathlib import Path

def ref(name): return {'Ref':name}
def attr(name,key): return {'Fn::GetAtt':[name,key]}
def sub(value,variables=None): return {'Fn::Sub':[value,variables]} if variables else {'Fn::Sub':value}
def join(values): return {'Fn::Join':['',values]}


def template():
    r={}
    tags=[{'Key':'Project','Value':'rewindguard-x'}]
    def add(name,kind,props,**extra):
        r[name]={'Type':kind,'Properties':props,**extra}
        return ref(name)
    core_table=sub('arn:${AWS::Partition}:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${AuthorityTableName}')
    bucket=sub('arn:${AWS::Partition}:s3:::${DocumentBucketName}')
    authority_policy={'Effect':'Allow','Action':['dynamodb:GetItem','dynamodb:PutItem'],'Resource':core_table}
    control_policy={'Effect':'Allow','Action':['dynamodb:GetItem','dynamodb:PutItem'],'Resource':attr('ControlTable','Arn')}
    def controls(keys,write=False):
        return {'Effect':'Allow','Action':['dynamodb:GetItem','dynamodb:PutItem'] if write else ['dynamodb:GetItem'],
            'Resource':attr('ControlTable','Arn'),'Condition':{'ForAllValues:StringLike':{'dynamodb:LeadingKeys':keys}}}
    s3_policy=[{'Effect':'Allow','Action':['s3:ListBucketVersions'],'Resource':bucket},
               {'Effect':'Allow','Action':['s3:GetObject','s3:GetObjectVersion','s3:PutObject','s3:DeleteObjectVersion'],
                'Resource':[sub('arn:${AWS::Partition}:s3:::${DocumentBucketName}/synthetic/*'),
                            sub('arn:${AWS::Partition}:s3:::${DocumentBucketName}/evidence/*')]}]
    add('Vpc','AWS::EC2::VPC',{'CidrBlock':'10.43.0.0/16','EnableDnsSupport':True,'EnableDnsHostnames':True,'Tags':tags})
    for number,zone in [(1,0),(2,1)]:
        add('Subnet'+str(number),'AWS::EC2::Subnet',{'VpcId':ref('Vpc'),'CidrBlock':f'10.43.{number}.0/24',
            'AvailabilityZone':{'Fn::Select':[zone,{'Fn::GetAZs':''}]},'MapPublicIpOnLaunch':False,'Tags':tags})
        add('RouteTable'+str(number),'AWS::EC2::RouteTable',{'VpcId':ref('Vpc'),'Tags':tags})
        add('SubnetRoute'+str(number),'AWS::EC2::SubnetRouteTableAssociation',{'SubnetId':ref('Subnet'+str(number)),'RouteTableId':ref('RouteTable'+str(number))})
    for service in ('s3','dynamodb'):
        add(service.title()+'Endpoint','AWS::EC2::VPCEndpoint',{'VpcId':ref('Vpc'),'VpcEndpointType':'Gateway',
            'ServiceName':sub('com.amazonaws.${AWS::Region}.'+service),'RouteTableIds':[ref('RouteTable1'),ref('RouteTable2')]})
    for name in ('DataSg','RecoverySg'):
        add(name,'AWS::EC2::SecurityGroup',{'GroupDescription':'Private Lambda network access','VpcId':ref('Vpc'),'Tags':tags})
    for name,sources in [('LiveDbSg',['DataSg']),('CandidateDbSg',['RecoverySg']),('ServingDbSg',['DataSg','RecoverySg'])]:
        add(name,'AWS::EC2::SecurityGroup',{'GroupDescription':name,'VpcId':ref('Vpc'),'Tags':tags,
            'SecurityGroupIngress':[{'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'SourceSecurityGroupId':ref(group)} for group in sources]})
    add('SecretEndpointSg','AWS::EC2::SecurityGroup',{'GroupDescription':'Bootstrap secret access only','VpcId':ref('Vpc'),
        'SecurityGroupIngress':[{'IpProtocol':'tcp','FromPort':443,'ToPort':443,'SourceSecurityGroupId':ref('DataSg')}],'Tags':tags},Condition='BootstrapEnabled')
    add('SecretEndpoint','AWS::EC2::VPCEndpoint',{'VpcId':ref('Vpc'),'VpcEndpointType':'Interface','PrivateDnsEnabled':True,
        'ServiceName':sub('com.amazonaws.${AWS::Region}.secretsmanager'),'SubnetIds':[ref('Subnet1')],
        'SecurityGroupIds':[ref('SecretEndpointSg')]},Condition='BootstrapEnabled')
    add('DbSubnetGroup','AWS::RDS::DBSubnetGroup',{'DBSubnetGroupDescription':'Private RewindGuard database subnets',
        'SubnetIds':[ref('Subnet1'),ref('Subnet2')],'Tags':tags})
    add('DbSecret','AWS::SecretsManager::Secret',{'Description':'RDS bootstrap credential, never used by serving functions',
        'GenerateSecretString':{'SecretStringTemplate':'{"username":"rgx_admin"}','GenerateStringKey':'password','PasswordLength':32,'ExcludePunctuation':True},'Tags':tags})
    add('Database','AWS::RDS::DBInstance',{'DBInstanceIdentifier':'rgx-live','Engine':'postgres','EngineVersion':ref('DbEngineVersion'),
        'DBInstanceClass':'db.t4g.micro','AllocatedStorage':'20','StorageType':'gp3','StorageEncrypted':True,
        'DBName':'rgx','MasterUsername':'rgx_admin','MasterUserPassword':join(['{{resolve:secretsmanager:',ref('DbSecret'),':SecretString:password}}']),
        'DBSubnetGroupName':ref('DbSubnetGroup'),'VPCSecurityGroups':[ref('LiveDbSg')],
        'AvailabilityZone':attr('Subnet1','AvailabilityZone'),'PubliclyAccessible':False,'MultiAZ':False,
        'EnableIAMDatabaseAuthentication':True,'BackupRetentionPeriod':0,'CopyTagsToSnapshot':True,
        'DeletionProtection':False,'AutoMinorVersionUpgrade':True,'EnableCloudwatchLogsExports':['postgresql'],
        'Tags':tags+[{'Key':'ExpiresAt','Value':ref('DemoExpiresAt')}]},Condition='SourceDatabaseEnabled',DeletionPolicy='Delete',UpdateReplacePolicy='Delete')
    add('ControlTable','AWS::DynamoDB::Table',{'AttributeDefinitions':[{'AttributeName':'pk','AttributeType':'S'}],
        'KeySchema':[{'AttributeName':'pk','KeyType':'HASH'}],'BillingMode':'PAY_PER_REQUEST','Tags':tags})
    add('EvidenceKey','AWS::KMS::Key',{'Description':'RewindGuard evidence signatures','KeySpec':'ECC_NIST_P256','KeyUsage':'SIGN_VERIFY',
        'KeyPolicy':{'Version':'2012-10-17','Statement':[{'Sid':'AccountIamDelegation','Effect':'Allow',
           'Principal':{'AWS':sub('arn:${AWS::Partition}:iam::${AWS::AccountId}:root')},'Action':'kms:*','Resource':'*'}]},
        'PendingWindowInDays':7,'Tags':tags})
    signer_policy={'Effect':'Allow','Action':['kms:Sign','kms:Verify','kms:GetPublicKey'],'Resource':attr('EvidenceKey','Arn')}
    eni={'Effect':'Allow','Action':['ec2:CreateNetworkInterface','ec2:DescribeNetworkInterfaces','ec2:DescribeSubnets','ec2:DeleteNetworkInterface',
                                  'ec2:AssignPrivateIpAddresses','ec2:UnassignPrivateIpAddresses'],'Resource':'*'}
    def role(name,statements,vpc=False,condition=None):
        policies=[{'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],
            'Resource':sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/rgx-*:*')},*statements]
        if vpc: policies.append(eni)
        return add(name,'AWS::IAM::Role',{'AssumeRolePolicyDocument':{'Version':'2012-10-17','Statement':[
            {'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}]},
            'Policies':[{'PolicyName':'scoped-project-access','PolicyDocument':{'Version':'2012-10-17','Statement':policies}}],
            'Tags':tags},**({'Condition':condition} if condition else {}))
    role('DataRole',[authority_policy,controls(['ACTIVE']),*s3_policy,{'Effect':'Allow','Action':'rds-db:connect',
        'Resource':sub('arn:${AWS::Partition}:rds-db:${AWS::Region}:${AWS::AccountId}:dbuser:*/rgx_app')}],vpc=True)
    role('CandidateRole',[{'Effect':'Allow','Action':'dynamodb:GetItem','Resource':core_table},controls(['CANDIDATE#*']),*s3_policy,{'Effect':'Allow','Action':'rds-db:connect',
        'Resource':sub('arn:${AWS::Partition}:rds-db:${AWS::Region}:${AWS::AccountId}:dbuser:*/rgx_recovery')}],vpc=True)
    role('BootstrapRole',[{'Effect':'Allow','Action':'secretsmanager:GetSecretValue','Resource':ref('DbSecret')}],vpc=True,condition='BootstrapEnabled')
    base={'AUTHORITY_TABLE':ref('AuthorityTableName'),'CONTROL_TABLE':ref('ControlTable'),'DOCUMENT_BUCKET':ref('DocumentBucketName'),
          'LIVE_DB_HOST':{'Fn::If':['SourceDatabaseEnabled',attr('Database','Endpoint.Address'),'disabled']}}
    code={'S3Bucket':ref('DocumentBucketName'),'S3Key':ref('CodeKey'),'S3ObjectVersion':ref('CodeVersion')}
    def function(name,handler,role_name,variables,vpc=None,condition=None):
        function_name='rgx-'+name.lower()
        add(name+'Log','AWS::Logs::LogGroup',{'LogGroupName':'/aws/lambda/'+function_name,'RetentionInDays':1,'Tags':tags},
            **({'Condition':condition} if condition else {}))
        props={'FunctionName':function_name,'Runtime':'python3.12','Architectures':['x86_64'],'Handler':handler,
            'Role':attr(role_name,'Arn'),'Code':code,'Timeout':120,'MemorySize':256,'ReservedConcurrentExecutions':2,
            'Environment':{'Variables':variables},'Tags':tags}
        if vpc: props['VpcConfig']={'SubnetIds':[ref('Subnet1')],'SecurityGroupIds':[ref(vpc)]}
        return add(name,'AWS::Lambda::Function',props,DependsOn=[name+'Log'],**({'Condition':condition} if condition else {}))
    function('DataFunction','backend.cloud.data.handler','DataRole',{**base,'WORKER_MODE':'live'},vpc='DataSg')
    function('CandidateFunction','backend.cloud.data.handler','CandidateRole',{**base,'WORKER_MODE':'candidate'},vpc='RecoverySg')
    function('BootstrapFunction','backend.cloud.data.bootstrap','BootstrapRole',{'LIVE_DB_HOST':{'Fn::If':['SourceDatabaseEnabled',attr('Database','Endpoint.Address'),'disabled']},
        'DB_SECRET_ARN':ref('DbSecret')},vpc='DataSg',condition='BootstrapEnabled')
    invoke_policy={'Effect':'Allow','Action':'lambda:InvokeFunction','Resource':[attr('DataFunction','Arn'),attr('CandidateFunction','Arn')]}
    rds_resources=[sub('arn:${AWS::Partition}:rds:${AWS::Region}:${AWS::AccountId}:db:rgx-live'),
        sub('arn:${AWS::Partition}:rds:${AWS::Region}:${AWS::AccountId}:db:rgx-candidate-*'),
        sub('arn:${AWS::Partition}:rds:${AWS::Region}:${AWS::AccountId}:snapshot:rgx-snapshot-*'),
        sub('arn:${AWS::Partition}:rds:${AWS::Region}:${AWS::AccountId}:subgrp:${DbSubnetGroup}')]
    role('RecoveryRole',[authority_policy,controls(['ACTIVE','CANDIDATE#*'],write=True),*s3_policy,signer_policy,invoke_policy,
        {'Effect':'Allow','Action':'dynamodb:ConditionCheckItem','Resource':core_table},
        {'Effect':'Allow','Action':['rds:DescribeDBInstances','rds:DescribeDBSnapshots','rds:ListTagsForResource'],'Resource':'*'},
        {'Effect':'Allow','Action':['rds:CreateDBSnapshot','rds:RestoreDBInstanceFromDBSnapshot','rds:ModifyDBInstance','rds:AddTagsToResource'],
         'Resource':rds_resources}])
    cloudbase={**base,'DATA_FUNCTION':ref('DataFunction'),'CANDIDATE_FUNCTION':ref('CandidateFunction'),
       'EVIDENCE_KEY':attr('EvidenceKey','Arn'),'LIVE_DB_ID':{'Fn::If':['SourceDatabaseEnabled',ref('Database'),'retired']},'CANDIDATE_DB_SG':ref('CandidateDbSg'),
       'SERVING_DB_SG':ref('ServingDbSg'),'DB_SUBNET_GROUP':ref('DbSubnetGroup'),'DB_AZ':attr('Subnet1','AvailabilityZone')}
    function('RecoveryFunction','backend.cloud.recovery.handler','RecoveryRole',cloudbase)
    add('WorkflowLog','AWS::Logs::LogGroup',{'LogGroupName':'/aws/vendedlogs/states/rgx-recovery','RetentionInDays':1,'Tags':tags})
    add('WorkflowRole','AWS::IAM::Role',{'AssumeRolePolicyDocument':{'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Principal':{'Service':'states.amazonaws.com'},'Action':'sts:AssumeRole'}]},
        'Policies':[{'PolicyName':'invoke-recovery-only','PolicyDocument':{'Version':'2012-10-17','Statement':[
            {'Effect':'Allow','Action':'lambda:InvokeFunction','Resource':attr('RecoveryFunction','Arn')},
            {'Effect':'Allow','Action':['logs:CreateLogDelivery','logs:GetLogDelivery','logs:UpdateLogDelivery','logs:DeleteLogDelivery',
             'logs:ListLogDeliveries','logs:PutResourcePolicy','logs:DescribeResourcePolicies','logs:DescribeLogGroups'],'Resource':'*'}]}}]})
    states={}
    def task(label,action,next_state,end=False):
        states['Set'+label]={'Type':'Pass','Result':action,'ResultPath':'$.action','Next':label}
        states[label]={'Type':'Task','Resource':'arn:aws:states:::lambda:invoke','Parameters':{
            'FunctionName':attr('RecoveryFunction','Arn'),'Payload.$':'$'},'OutputPath':'$.Payload',
            'TimeoutSeconds':150,'Catch':[{'ErrorEquals':['States.ALL'],'ResultPath':'$.failure','Next':'Hold'}],
            **({'End':True} if end else {'Next':next_state})}
    task('Snapshot','snapshot','SetSnapshotStatus')
    task('SnapshotStatus','snapshot_status','SnapshotReady')
    states['SnapshotReady']={'Type':'Choice','Choices':[{'Variable':'$.ready','BooleanEquals':True,'Next':'SetErase'}],'Default':'WaitSnapshot'}
    states['WaitSnapshot']={'Type':'Wait','Seconds':30,'Next':'SnapshotStatus'}
    task('Erase','erase','SetRestore')
    task('Restore','restore','SetRestoreStatus')
    task('RestoreStatus','restore_status','RestoreReady')
    states['RestoreReady']={'Type':'Choice','Choices':[{'Variable':'$.ready','BooleanEquals':True,'Next':'SetValidate'}],'Default':'WaitRestore'}
    states['WaitRestore']={'Type':'Wait','Seconds':30,'Next':'RestoreStatus'}
    task('Validate','validate','SetRelease')
    task('Release','release',None,end=True)
    states['Hold']={'Type':'Fail','Error':'RecoveryHeld','CausePath':'$.failure.Error'}
    add('RecoveryWorkflow','AWS::StepFunctions::StateMachine',{'StateMachineName':'rgx-recovery','StateMachineType':'STANDARD',
        'RoleArn':attr('WorkflowRole','Arn'),'Definition':{'StartAt':'SetSnapshot','TimeoutSeconds':3600,'States':states},
        'LoggingConfiguration':{'Level':'ALL','IncludeExecutionData':False,'Destinations':[{'CloudWatchLogsLogGroup':{'LogGroupArn':attr('WorkflowLog','Arn')}}]},'Tags':tags})
    add('UserPool','AWS::Cognito::UserPool',{'UserPoolName':'rgx-project-users','AdminCreateUserConfig':{'AllowAdminCreateUserOnly':True},
        'Policies':{'PasswordPolicy':{'MinimumLength':16,'RequireLowercase':True,'RequireUppercase':True,'RequireNumbers':True,'RequireSymbols':True,'TemporaryPasswordValidityDays':1}},
        'Schema':[{'Name':'tenant_id','AttributeDataType':'String','Mutable':False,'Required':False}],
        'AccountRecoverySetting':{'RecoveryMechanisms':[{'Name':'admin_only','Priority':1}]},'UserPoolTags':{'Project':'rewindguard-x'}})
    add('OperatorGroup','AWS::Cognito::UserPoolGroup',{'UserPoolId':ref('UserPool'),'GroupName':'operators','Description':'Synthetic lab operators'})
    add('HttpApi','AWS::ApiGatewayV2::Api',{'Name':'rgx-cloud-api','ProtocolType':'HTTP','DisableExecuteApiEndpoint':False,'Tags':{'Project':'rewindguard-x'}})
    api_url=sub('https://${HttpApi}.execute-api.${AWS::Region}.${AWS::URLSuffix}')
    add('UserPoolClient','AWS::Cognito::UserPoolClient',{'UserPoolId':ref('UserPool'),'ClientName':'rgx-pkce-web','GenerateSecret':False,
        'AllowedOAuthFlowsUserPoolClient':True,'AllowedOAuthFlows':['code'],'AllowedOAuthScopes':['openid','profile'],
        'SupportedIdentityProviders':['COGNITO'],'CallbackURLs':[join([api_url,'/callback'])],'LogoutURLs':[join([api_url,'/ui'])],
        'ExplicitAuthFlows':['ALLOW_USER_PASSWORD_AUTH','ALLOW_REFRESH_TOKEN_AUTH'],
        'ReadAttributes':['sub','custom:tenant_id'],'WriteAttributes':['name'],
        'PreventUserExistenceErrors':'ENABLED','EnableTokenRevocation':True,'AccessTokenValidity':15,'IdTokenValidity':15,
        'TokenValidityUnits':{'AccessToken':'minutes','IdToken':'minutes','RefreshToken':'days'},'RefreshTokenValidity':1})
    add('UserDomain','AWS::Cognito::UserPoolDomain',{'UserPoolId':ref('UserPool'),'Domain':sub('rgx-${AWS::AccountId}-${AWS::Region}')})
    auth_domain=sub('https://rgx-${AWS::AccountId}-${AWS::Region}.auth.${AWS::Region}.amazoncognito.com')
    role('HttpRole',[authority_policy,controls(['RUN#*'],write=True),*s3_policy,signer_policy,invoke_policy,
        {'Effect':'Allow','Action':'states:StartExecution','Resource':ref('RecoveryWorkflow')},
        {'Effect':'Allow','Action':'states:DescribeExecution','Resource':sub('arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:execution:rgx-recovery:*')}])
    function('HttpFunction','backend.cloud.http.handler','HttpRole',{**cloudbase,'USER_POOL_CLIENT':ref('UserPoolClient'),
        'AUTH_DOMAIN':auth_domain,'API_URL':api_url,'RECOVERY_STATE_MACHINE':ref('RecoveryWorkflow')})
    add('ApiIntegration','AWS::ApiGatewayV2::Integration',{'ApiId':ref('HttpApi'),'IntegrationType':'AWS_PROXY','IntegrationUri':attr('HttpFunction','Arn'),
        'PayloadFormatVersion':'2.0','TimeoutInMillis':29000})
    add('JwtAuthorizer','AWS::ApiGatewayV2::Authorizer',{'ApiId':ref('HttpApi'),'Name':'CognitoJwt','AuthorizerType':'JWT',
        'IdentitySource':['$request.header.Authorization'],'JwtConfiguration':{'Audience':[ref('UserPoolClient')],
        'Issuer':sub('https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}')}})
    for index,path in enumerate(['GET /','GET /ui','GET /callback','GET /app.js','GET /style.css','GET /health']):
        add('PublicRoute'+str(index),'AWS::ApiGatewayV2::Route',{'ApiId':ref('HttpApi'),'RouteKey':path,'AuthorizationType':'NONE',
            'Target':join(['integrations/',ref('ApiIntegration')])})
    add('AuthenticatedRoute','AWS::ApiGatewayV2::Route',{'ApiId':ref('HttpApi'),'RouteKey':'$default','AuthorizationType':'JWT','AuthorizerId':ref('JwtAuthorizer'),
        'Target':join(['integrations/',ref('ApiIntegration')])})
    add('ApiStage','AWS::ApiGatewayV2::Stage',{'ApiId':ref('HttpApi'),'StageName':'$default','AutoDeploy':True,
        'DefaultRouteSettings':{'ThrottlingRateLimit':2,'ThrottlingBurstLimit':5}})
    add('ApiInvokePermission','AWS::Lambda::Permission',{'FunctionName':ref('HttpFunction'),'Action':'lambda:InvokeFunction',
        'Principal':'apigateway.amazonaws.com','SourceArn':sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*')})
    role('CleanupRole',[{'Effect':'Allow','Action':['rds:DescribeDBInstances','rds:DescribeDBSnapshots','rds:ListTagsForResource'],'Resource':'*'},
         {'Effect':'Allow','Action':['rds:StopDBInstance','rds:DeleteDBInstance','rds:DeleteDBSnapshot'],'Resource':rds_resources},controls(['ACTIVE'])])
    function('CleanupFunction','backend.cloud.cleanup.handler','CleanupRole',{'CONTROL_TABLE':ref('ControlTable')})
    add('CleanupSchedule','AWS::Events::Rule',{'ScheduleExpression':'rate(30 minutes)','State':'ENABLED',
        'Targets':[{'Id':'cleanup','Arn':attr('CleanupFunction','Arn')}]})
    add('CleanupInvokePermission','AWS::Lambda::Permission',{'FunctionName':ref('CleanupFunction'),'Action':'lambda:InvokeFunction',
        'Principal':'events.amazonaws.com','SourceArn':attr('CleanupSchedule','Arn')})
    add('WorkflowFailureAlarm','AWS::CloudWatch::Alarm',{'AlarmDescription':'Inspect held or failed recovery workflows','Namespace':'AWS/States',
        'MetricName':'ExecutionsFailed','Dimensions':[{'Name':'StateMachineArn','Value':ref('RecoveryWorkflow')}],
        'Statistic':'Sum','Period':300,'EvaluationPeriods':1,'Threshold':1,'ComparisonOperator':'GreaterThanOrEqualToThreshold','TreatMissingData':'notBreaching'})
    add('ApiErrorAlarm','AWS::CloudWatch::Alarm',{'AlarmDescription':'Inspect cloud API invocation failures','Namespace':'AWS/Lambda',
        'MetricName':'Errors','Dimensions':[{'Name':'FunctionName','Value':ref('HttpFunction')}],
        'Statistic':'Sum','Period':300,'EvaluationPeriods':1,'Threshold':1,'ComparisonOperator':'GreaterThanOrEqualToThreshold','TreatMissingData':'notBreaching'})
    return {'AWSTemplateFormatVersion':'2010-09-09','Description':'RewindGuard private RDS and authenticated cloud recovery research deployment',
        'Parameters':{**{key:{'Type':'String'} for key in ['AuthorityTableName','DocumentBucketName','CodeKey','CodeVersion','DbEngineVersion','DemoExpiresAt']},
            'EnableBootstrap':{'Type':'String','Default':'true','AllowedValues':['true','false']},
            'RetainOriginalDatabase':{'Type':'String','Default':'true','AllowedValues':['true','false']}},
        'Conditions':{'BootstrapEnabled':{'Fn::Equals':[ref('EnableBootstrap'),'true']},'SourceDatabaseEnabled':{'Fn::Equals':[ref('RetainOriginalDatabase'),'true']}},'Resources':r,
        'Outputs':{key:{'Value':value} for key,value in {'ApiUrl':api_url,'UserPoolId':ref('UserPool'),'UserPoolClientId':ref('UserPoolClient'),
            'AuthDomain':auth_domain,'EvidenceKeyArn':attr('EvidenceKey','Arn'),'ControlTableName':ref('ControlTable'),
            'DataFunctionName':ref('DataFunction'),'CandidateFunctionName':ref('CandidateFunction'),'RecoveryFunctionName':ref('RecoveryFunction'),
            'RecoveryStateMachineArn':ref('RecoveryWorkflow'),'DatabaseId':{'Fn::If':['SourceDatabaseEnabled',ref('Database'),'retired']},
            'BootstrapFunctionName':{'Fn::If':['BootstrapEnabled',ref('BootstrapFunction'),'disabled']},
            'CleanupFunctionName':ref('CleanupFunction')}.items()}}


if __name__=='__main__':
    destination=Path(__file__).with_name('application.json')
    destination.write_text(json.dumps(template(),separators=(',',':'))+'\n',encoding='utf-8')
    print('Generated private application template')
