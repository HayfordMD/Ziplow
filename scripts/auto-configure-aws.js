#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { STSClient, GetCallerIdentityCommand } from '@aws-sdk/client-sts';
import { SESClient, ListIdentitiesCommand, GetIdentityVerificationAttributesCommand } from '@aws-sdk/client-ses';
import config from '../src/config.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const envPath = path.join(rootDir, '.env');

// Support passing keys via CLI arguments: node scripts/auto-configure-aws.js <KEY_ID> <SECRET_KEY> [REGION]
const [,, argKeyId, argSecret, argRegion] = process.argv;

const accessKeyId = argKeyId || config.aws.accessKeyId;
const secretAccessKey = argSecret || config.aws.secretAccessKey;
let region = argRegion || config.aws.region || 'us-east-1';

function updateEnvFile(updates) {
  let envContent = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf-8') : '';

  for (const [key, val] of Object.entries(updates)) {
    if (val === undefined || val === null) continue;
    const regex = new RegExp(`^${key}=.*$`, 'm');
    if (regex.test(envContent)) {
      envContent = envContent.replace(regex, `${key}=${val}`);
    } else {
      envContent += `\n${key}=${val}`;
    }
  }

  fs.writeFileSync(envPath, envContent.trim() + '\n', 'utf-8');
}

function deriveSenderId(domainName, fallback = 'MYAPP') {
  if (!domainName) return fallback;
  // AWS SNS Sender ID must be 1-11 alphanumeric characters, at least one letter, no spaces
  const clean = domainName.split('.')[0].replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
  return clean.slice(0, 11) || fallback;
}

async function main() {
  console.log(`\n============================================================`);
  console.log(`🪄  AWS CREDENTIALS AUTO-GENERATOR & DISCOVERY TOOL`);
  console.log(`============================================================\n`);

  if (!accessKeyId || !secretAccessKey) {
    console.error(`❌ AWS Credentials not found.`);
    console.log(`Usage:`);
    console.log(`  node scripts/auto-configure-aws.js <AWS_ACCESS_KEY_ID> <AWS_SECRET_ACCESS_KEY> [REGION]`);
    console.log(`Or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file.\n`);
    process.exit(1);
  }

  console.log(`1. Validating AWS Credentials via AWS STS...`);
  let accountId = '';
  let userArn = '';
  try {
    const sts = new STSClient({
      region,
      credentials: { accessKeyId, secretAccessKey },
    });
    const identity = await sts.send(new GetCallerIdentityCommand({}));
    accountId = identity.Account;
    userArn = identity.Arn;
    console.log(`   ✅ Valid AWS Credentials!`);
    console.log(`   Account ID: ${accountId}`);
    console.log(`   IAM ARN:    ${userArn}`);
  } catch (err) {
    console.error(`   ❌ Failed to validate credentials:`, err.message);
    process.exit(1);
  }

  console.log(`\n2. Inspecting AWS SES Identities in ${region}...`);
  const ses = new SESClient({
    region,
    credentials: { accessKeyId, secretAccessKey },
  });

  let existingEmails = [];
  let existingDomains = [];

  try {
    const emailRes = await ses.send(new ListIdentitiesCommand({ IdentityType: 'EmailAddress' }));
    existingEmails = emailRes.Identities || [];

    const domainRes = await ses.send(new ListIdentitiesCommand({ IdentityType: 'Domain' }));
    existingDomains = domainRes.Identities || [];
  } catch (sesErr) {
    console.warn(`   ⚠️ Warning querying SES identities (${sesErr.message}).`);
  }

  // Check verification statuses
  let verifiedEmail = null;
  if (existingEmails.length > 0) {
    try {
      const attrs = await ses.send(new GetIdentityVerificationAttributesCommand({ Identities: existingEmails }));
      for (const email of existingEmails) {
        if (attrs.VerificationAttributes?.[email]?.VerificationStatus === 'Success') {
          verifiedEmail = email;
          break;
        }
      }
    } catch {}
  }

  console.log(`   Registered Emails in SES:  ${existingEmails.length > 0 ? existingEmails.join(', ') : 'None'}`);
  console.log(`   Registered Domains in SES: ${existingDomains.length > 0 ? existingDomains.join(', ') : 'None'}`);
  if (verifiedEmail) {
    console.log(`   Verified Sender Found:     ${verifiedEmail}`);
  }

  // 3. Compute Auto-Generated Values
  const domain = config.domain.name || existingDomains[0] || '';
  
  let fromEmail = '';
  let replyTo = '';

  if (verifiedEmail) {
    fromEmail = verifiedEmail;
    replyTo = verifiedEmail;
  } else if (domain) {
    fromEmail = `noreply@${domain}`;
    replyTo = `support@${domain}`;
  } else if (existingEmails.length > 0) {
    fromEmail = existingEmails[0];
    replyTo = existingEmails[0];
  } else {
    fromEmail = 'noreply@example.com';
    replyTo = 'support@example.com';
  }

  const senderId = deriveSenderId(domain || fromEmail.split('@')[1]);

  console.log(`\n============================================================`);
  console.log(`✨ AUTO-GENERATED CONFIGURATION VALUES:`);
  console.log(`============================================================`);
  console.log(`  AWS_ACCESS_KEY_ID     = ${accessKeyId.slice(0, 4)}...${accessKeyId.slice(-4)}`);
  console.log(`  AWS_SECRET_ACCESS_KEY = (Protected - ${secretAccessKey.slice(0, 3)}...)`);
  console.log(`  AWS_REGION            = ${region}`);
  console.log(`  AWS_SES_FROM_EMAIL    = ${fromEmail}`);
  console.log(`  AWS_SES_REPLY_TO      = ${replyTo}`);
  console.log(`  AWS_SNS_SENDER_ID     = ${senderId}`);
  if (!config.domain.name && existingDomains[0]) {
    console.log(`  DOMAIN_NAME           = ${existingDomains[0]} (Discovered from SES)`);
  }
  console.log(`============================================================\n`);

  // 4. Save to .env
  const updates = {
    AWS_ACCESS_KEY_ID: accessKeyId,
    AWS_SECRET_ACCESS_KEY: secretAccessKey,
    AWS_REGION: region,
    AWS_SES_FROM_EMAIL: fromEmail,
    AWS_SES_REPLY_TO: replyTo,
    AWS_SNS_SENDER_ID: senderId,
  };

  if (!config.domain.name && existingDomains[0]) {
    updates.DOMAIN_NAME = existingDomains[0];
  }

  updateEnvFile(updates);
  console.log(`✅ Automatically saved all generated values to .env!`);
  console.log(`🔒 Your .env file is git-ignored and secure.`);

  console.log(`\n👉 Next Steps:`);
  console.log(`  1. Verify everything: npm run check`);
  if (domain) {
    console.log(`  2. Setup SES verification & auto-push DNS to GoDaddy: npm run ses:verify`);
  } else {
    console.log(`  2. Set your DOMAIN_NAME in .env if you have a custom domain.`);
  }
  console.log(`  3. Test sending an email: npm run ses:test\n`);
}

main();
