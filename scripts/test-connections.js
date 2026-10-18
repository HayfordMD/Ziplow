#!/usr/bin/env node
import config from '../src/config.js';
import { godaddy } from '../src/services/godaddy.js';
import { awsEmail } from '../src/services/awsSes.js';
import { makersHost } from '../src/services/makershost.js';
import { ncb } from '../src/services/nocodebackend.js';

async function testGoDaddy() {
  if (!config.domain.name) {
    return { status: 'SKIPPED', message: 'DOMAIN_NAME not specified in .env' };
  }
  if (!config.domain.apiKey) {
    return { status: 'CONFIG_NEEDED', message: 'GODADDY_API_KEY missing in .env' };
  }
  try {
    const res = await godaddy.verifyDomain();
    return { status: 'OK', message: `Domain "${config.domain.name}" verified (${res.status || 'Active'})` };
  } catch (err) {
    return { status: 'ERROR', message: err.message };
  }
}

async function testAws() {
  if (!config.aws.accessKeyId || !config.aws.secretAccessKey) {
    return { status: 'CONFIG_NEEDED', message: 'AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY missing in .env' };
  }
  try {
    const quota = await awsEmail.getQuota();
    let sesMsg = `SES operational (Max 24h: ${quota.Max24HourSend})`;
    if (config.domain.name) {
      try {
        const domStatus = await awsEmail.getDomainStatus(config.domain.name);
        sesMsg += ` | Domain: ${domStatus.verificationStatus}`;
      } catch {}
    }
    return { status: 'OK', message: sesMsg };
  } catch (err) {
    return { status: 'ERROR', message: err.message };
  }
}

async function testMakersHost() {
  if (!config.storage.apiKey) {
    return { status: 'CONFIG_NEEDED', message: 'PUBLIC_STORAGE_API_KEY missing in .env' };
  }
  try {
    const ping = await makersHost.ping();
    if (ping.ok) {
      return { status: 'OK', message: `Storage cluster online at ${config.storage.uploadUrl}` };
    }
    return { status: 'WARN', message: `Cluster responded with status ${ping.status}` };
  } catch (err) {
    return { status: 'ERROR', message: err.message };
  }
}

async function testNcb() {
  if (!config.ncb.instance) {
    return { status: 'CONFIG_NEEDED', message: 'NCB_INSTANCE missing in .env' };
  }
  try {
    const conn = await ncb.testConnection();
    if (conn.ok) {
      return { status: 'OK', message: `NCB instance "${config.ncb.instance}" online` };
    }
    return { status: 'WARN', message: `NCB instance returned status ${conn.status}` };
  } catch (err) {
    return { status: 'ERROR', message: err.message };
  }
}

async function testStripe() {
  if (!config.stripe.secretKey) {
    return { status: 'CONFIG_NEEDED', message: 'STRIPE_SECRET_KEY missing in .env' };
  }
  try {
    const conn = await (await import('../src/services/stripe.js')).stripeService.testConnection();
    if (conn.ok) {
      return { status: 'OK', message: `Stripe online (${conn.mode.toUpperCase()} mode)` };
    }
    return { status: 'ERROR', message: conn.error };
  } catch (err) {
    return { status: 'ERROR', message: err.message };
  }
}

async function main() {
  console.log(`\n============================================================`);
  console.log(`🔍 BACKEND BOILERPLATE: COMPREHENSIVE CONNECTION AUDIT`);
  console.log(`============================================================\n`);

  console.log('Testing services...\n');
  const [gd, aws, storage, ncbRes, stripeRes] = await Promise.all([
    testGoDaddy(),
    testAws(),
    testMakersHost(),
    testNcb(),
    testStripe(),
  ]);

  const report = [
    { Service: '1. Domain & GoDaddy DNS', Status: gd.status, Details: gd.message },
    { Service: '2. AWS SES Email / IAM', Status: aws.status, Details: aws.message },
    { Service: '3. MakersHost Storage', Status: storage.status, Details: storage.message },
    { Service: '4. NoCodeBackend (NCB)', Status: ncbRes.status, Details: ncbRes.message },
    { Service: '5. Stripe Payments', Status: stripeRes.status, Details: stripeRes.message },
  ];

  console.table(report);

  console.log(`\nNext Steps:`);
  if (report.some(r => r.Status === 'CONFIG_NEEDED')) {
    console.log(`  👉 Update missing keys in your .env file.`);
  }
  if (gd.Status === 'OK') {
    console.log(`  👉 Manage GoDaddy DNS: npm run dns:list`);
  }
  if (aws.Status === 'OK') {
    console.log(`  👉 Verify SES domain & DKIM: npm run ses:verify`);
    console.log(`  👉 Deploy Serverless Lambda: npm run deploy:lambda`);
  }
  if (storage.Status === 'OK') {
    console.log(`  👉 Test Storage Upload: npm run test:storage`);
  }
  console.log('');
}

main();
