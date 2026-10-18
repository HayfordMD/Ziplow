#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import AdmZip from 'adm-zip';
import {
  LambdaClient,
  GetFunctionCommand,
  CreateFunctionCommand,
  UpdateFunctionCodeCommand,
  UpdateFunctionConfigurationCommand,
  GetFunctionUrlConfigCommand,
  CreateFunctionUrlConfigCommand,
  AddPermissionCommand,
} from '@aws-sdk/client-lambda';
import {
  IAMClient,
  GetRoleCommand,
  CreateRoleCommand,
  AttachRolePolicyCommand,
} from '@aws-sdk/client-iam';
import config from '../src/config.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');

const REGION = config.aws.region || 'us-east-1';
const FUNCTION_NAME = config.aws.lambda.functionName || 'backend-serverless-api';

const lambdaClient = new LambdaClient({
  region: REGION,
  credentials: {
    accessKeyId: config.aws.accessKeyId,
    secretAccessKey: config.aws.secretAccessKey,
  },
});

const iamClient = new IAMClient({
  region: REGION,
  credentials: {
    accessKeyId: config.aws.accessKeyId,
    secretAccessKey: config.aws.secretAccessKey,
  },
});

async function ensureExecutionRole() {
  if (config.aws.lambda.roleArn) {
    return config.aws.lambda.roleArn;
  }

  const roleName = 'BackendLambdaBasicExecutionRole';
  console.log(`🔍 Checking IAM Execution Role "${roleName}"...`);

  try {
    const res = await iamClient.send(new GetRoleCommand({ RoleName: roleName }));
    console.log(`✅ Using existing IAM role: ${res.Role.Arn}`);
    return res.Role.Arn;
  } catch (err) {
    if (err.name !== 'NoSuchEntityException' && !err.message?.includes('cannot be found')) {
      throw err;
    }
  }

  console.log(`🚀 Creating IAM Execution Role "${roleName}"...`);
  const assumeRolePolicy = JSON.stringify({
    Version: '2012-10-17',
    Statement: [
      {
        Effect: 'Allow',
        Principal: { Service: 'lambda.amazonaws.com' },
        Action: 'sts:AssumeRole',
      },
    ],
  });

  const createRoleRes = await iamClient.send(
    new CreateRoleCommand({
      RoleName: roleName,
      AssumeRolePolicyDocument: assumeRolePolicy,
      Description: 'Execution role for Backend Serverless Lambda function',
    })
  );

  const roleArn = createRoleRes.Role.Arn;

  // Attach basic Lambda execution policy (CloudWatch logs)
  await iamClient.send(
    new AttachRolePolicyCommand({
      RoleName: roleName,
      PolicyArn: 'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
    })
  );

  // Attach SES sending policy
  try {
    await iamClient.send(
      new AttachRolePolicyCommand({
        RoleName: roleName,
        PolicyArn: 'arn:aws:iam::aws:policy/AmazonSESFullAccess',
      })
    );
  } catch (sesErr) {
    console.warn(`Note: Could not attach AmazonSESFullAccess to role: ${sesErr.message}`);
  }

  console.log(`✅ Created IAM Role: ${roleArn}`);
  console.log(`⏳ Waiting 8s for AWS IAM role propagation...`);
  await new Promise((resolve) => setTimeout(resolve, 8000));
  return roleArn;
}

function packageZip() {
  console.log(`📦 Packaging deployment zip bundle...`);
  const zip = new AdmZip();

  // Add src/
  zip.addLocalFolder(path.join(rootDir, 'src'), 'src');

  // Add package.json
  zip.addLocalFile(path.join(rootDir, 'package.json'));

  // Add node_modules (excluding test/cache artifacts)
  const nodeModulesPath = path.join(rootDir, 'node_modules');
  if (fs.existsSync(nodeModulesPath)) {
    zip.addLocalFolder(nodeModulesPath, 'node_modules');
  }

  const zipBuffer = zip.toBuffer();
  console.log(`✅ Zip bundle created (${(zipBuffer.length / (1024 * 1024)).toFixed(2)} MB)`);
  return zipBuffer;
}

function getLambdaEnvVariables() {
  const envVars = {
    NODE_ENV: 'production',
    DOMAIN_NAME: config.domain.name || '',
    AWS_REGION: REGION,
    AWS_SES_FROM_EMAIL: config.aws.ses.fromEmail || '',
    AWS_SES_REPLY_TO: config.aws.ses.replyTo || '',
    PUBLIC_STORAGE_PROVIDER: config.storage.provider || 'makershost',
    PUBLIC_STORAGE_UPLOAD_URL: config.storage.uploadUrl || '',
    PUBLIC_STORAGE_API_KEY: config.storage.apiKey || '',
    PUBLIC_STORAGE_DEFAULT_FOLDER: config.storage.defaultFolder || 'backend_uploads',
    NCB_INSTANCE: config.ncb.instance || '',
    NCB_SECRET_KEY: config.ncb.secretKey || '',
    NCB_AUTH_API_URL: config.ncb.authUrl || '',
    NCB_DATA_API_URL: config.ncb.dataUrl || '',
    NCB_APP_URL: config.ncb.appUrl || '',
    STRIPE_SECRET_KEY: config.stripe.secretKey || '',
    STRIPE_PUBLISHABLE_KEY: config.stripe.publishableKey || '',
    STRIPE_WEBHOOK_SECRET: config.stripe.webhookSecret || '',
    STRIPE_MONTHLY_PRICE_ID: config.stripe.monthlyPriceId || '',
    STRIPE_YEARLY_PRICE_ID: config.stripe.yearlyPriceId || '',
    STRIPE_MONTHLY_LINK: config.stripe.monthlyLink || '',
    STRIPE_YEARLY_LINK: config.stripe.yearlyLink || '',
    STRIPE_CURRENCY: config.stripe.currency || 'usd',
  };

  // Filter out empty keys
  const cleaned = {};
  for (const [k, v] of Object.entries(envVars)) {
    if (v) cleaned[k] = String(v);
  }
  return cleaned;
}

async function ensureFunctionUrl(functionName) {
  try {
    const res = await lambdaClient.send(
      new GetFunctionUrlConfigCommand({ FunctionName: functionName })
    );
    return res.FunctionUrl;
  } catch (err) {
    if (err.name !== 'ResourceNotFoundException') {
      throw err;
    }
  }

  console.log(`🌐 Creating AWS Lambda Function URL (Public HTTPS endpoint)...`);
  const urlRes = await lambdaClient.send(
    new CreateFunctionUrlConfigCommand({
      FunctionName: functionName,
      AuthType: 'NONE',
      Cors: {
        AllowOrigins: ['*'],
        AllowMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
        AllowHeaders: ['*'],
        AllowCredentials: true,
      },
    })
  );

  // Add permission for public invoke
  try {
    await lambdaClient.send(
      new AddPermissionCommand({
        FunctionName: functionName,
        StatementId: 'FunctionURLAllowPublicAccess',
        Action: 'lambda:InvokeFunctionUrl',
        Principal: '*',
        FunctionUrlAuthType: 'NONE',
      })
    );
  } catch (permErr) {
    // Statement already exists or allowed
  }

  return urlRes.FunctionUrl;
}

async function main() {
  if (!config.aws.accessKeyId || !config.aws.secretAccessKey) {
    console.error(`❌ AWS Credentials missing. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env`);
    process.exit(1);
  }

  console.log(`\n============================================================`);
  console.log(`🚀 AWS Lambda Deployment`);
  console.log(`📌 Function: ${FUNCTION_NAME}`);
  console.log(`🌍 Region:   ${REGION}`);
  console.log(`============================================================\n`);

  try {
    const roleArn = await ensureExecutionRole();
    const zipBuffer = packageZip();
    const envVars = getLambdaEnvVariables();

    let functionExists = false;
    try {
      await lambdaClient.send(new GetFunctionCommand({ FunctionName: FUNCTION_NAME }));
      functionExists = true;
    } catch (err) {
      if (err.name !== 'ResourceNotFoundException') {
        throw err;
      }
    }

    if (functionExists) {
      console.log(`🔄 Updating existing Lambda function code...`);
      await lambdaClient.send(
        new UpdateFunctionCodeCommand({
          FunctionName: FUNCTION_NAME,
          ZipFile: zipBuffer,
        })
      );

      console.log(`⚙️ Updating Lambda function configuration & environment variables...`);
      await lambdaClient.send(
        new UpdateFunctionConfigurationCommand({
          FunctionName: FUNCTION_NAME,
          Runtime: config.aws.lambda.runtime,
          Handler: 'src/lambda/index.handler',
          MemorySize: config.aws.lambda.memorySize,
          Timeout: config.aws.lambda.timeout,
          Environment: { Variables: envVars },
        })
      );
      console.log(`✅ Lambda code and configuration updated!`);
    } else {
      console.log(`🚀 Creating new Lambda function "${FUNCTION_NAME}"...`);
      await lambdaClient.send(
        new CreateFunctionCommand({
          FunctionName: FUNCTION_NAME,
          Runtime: config.aws.lambda.runtime,
          Role: roleArn,
          Handler: 'src/lambda/index.handler',
          Code: { ZipFile: zipBuffer },
          Description: 'Backend serverless API deployed via backend-build',
          Timeout: config.aws.lambda.timeout,
          MemorySize: config.aws.lambda.memorySize,
          Environment: { Variables: envVars },
        })
      );
      console.log(`✅ Lambda function created successfully!`);
    }

    // Configure Function URL
    const functionUrl = await ensureFunctionUrl(FUNCTION_NAME);

    console.log(`\n============================================================`);
    console.log(`🎉 DEPLOYMENT COMPLETE!`);
    console.log(`🔗 Live HTTPS URL: ${functionUrl}`);
    console.log(`📡 Health Check:  ${functionUrl}api/health`);
    console.log(`============================================================\n`);

    if (config.domain.name) {
      const urlHost = new URL(functionUrl).hostname;
      console.log(`💡 Want to connect your custom domain to this Lambda?`);
      console.log(`   Run: node scripts/godaddy-dns.js set-cname api ${urlHost}\n`);
    }
  } catch (err) {
    console.error(`\n❌ Deployment Failed:`, err);
    process.exit(1);
  }
}

main();
