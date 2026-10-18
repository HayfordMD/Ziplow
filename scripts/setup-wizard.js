#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const envPath = path.join(rootDir, '.env');
const examplePath = path.join(rootDir, '.env.example');

async function main() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  console.log(`\n============================================================`);
  console.log(`🛠️  BACKEND DEPLOYMENT BOILERPLATE SETUP WIZARD`);
  console.log(`============================================================`);
  console.log(`This wizard configures your .env file for:`);
  console.log(`  1. Domain & GoDaddy DNS Automation`);
  console.log(`  2. AWS SES Email & Lambda Backend`);
  console.log(`  3. MakersHost Storage`);
  console.log(`  4. NoCodeBackend (NCB) Database`);
  console.log(`============================================================\n`);

  // Load existing .env if present
  let envContent = '';
  if (fs.existsSync(envPath)) {
    envContent = fs.readFileSync(envPath, 'utf-8');
  } else if (fs.existsSync(examplePath)) {
    envContent = fs.readFileSync(examplePath, 'utf-8');
  }

  const getEnvVal = (key) => {
    const match = envContent.match(new RegExp(`^${key}=(.*)$`, 'm'));
    return match ? match[1].trim() : '';
  };

  const setEnvVal = (key, val) => {
    const regex = new RegExp(`^${key}=.*$`, 'm');
    if (regex.test(envContent)) {
      envContent = envContent.replace(regex, `${key}=${val}`);
    } else {
      envContent += `\n${key}=${val}`;
    }
  };

  try {
    console.log(`--- [1/4] DOMAIN & GODADDY DNS ---`);
    const domain = await rl.question(`Enter your Domain Name [${getEnvVal('DOMAIN_NAME') || 'e.g. myapp.com'}]: `);
    if (domain) setEnvVal('DOMAIN_NAME', domain);

    const gdKey = await rl.question(`Enter GoDaddy API Key [${getEnvVal('GODADDY_API_KEY') ? '***' : 'optional for auto-DNS'}]: `);
    if (gdKey) setEnvVal('GODADDY_API_KEY', gdKey);

    const gdSecret = await rl.question(`Enter GoDaddy API Secret [${getEnvVal('GODADDY_API_SECRET') ? '***' : 'optional'}]: `);
    if (gdSecret) setEnvVal('GODADDY_API_SECRET', gdSecret);

    console.log(`\n--- [2/4] AWS CREDENTIALS & SES / LAMBDA ---`);
    const awsKey = await rl.question(`Enter AWS Access Key ID [${getEnvVal('AWS_ACCESS_KEY_ID') || 'AKIA...' }]: `);
    if (awsKey) setEnvVal('AWS_ACCESS_KEY_ID', awsKey);

    const awsSecret = await rl.question(`Enter AWS Secret Access Key [${getEnvVal('AWS_SECRET_ACCESS_KEY') ? '***' : ''}]: `);
    if (awsSecret) setEnvVal('AWS_SECRET_ACCESS_KEY', awsSecret);

    const awsRegion = await rl.question(`Enter AWS Region [${getEnvVal('AWS_REGION') || 'us-east-1'}]: `) || 'us-east-1';
    setEnvVal('AWS_REGION', awsRegion);

    const sesFrom = await rl.question(`Enter Verified Sender Email [${getEnvVal('AWS_SES_FROM_EMAIL') || 'noreply@' + (getEnvVal('DOMAIN_NAME') || 'example.com')}]: `);
    if (sesFrom) setEnvVal('AWS_SES_FROM_EMAIL', sesFrom);

    console.log(`\n--- [3/4] MAKERSHOST STORAGE ---`);
    const storageUrl = await rl.question(`Enter Storage Upload URL [${getEnvVal('PUBLIC_STORAGE_UPLOAD_URL') || 'http://storage.bannkd.com/api/upload.php'}]: `) || 'http://storage.bannkd.com/api/upload.php';
    setEnvVal('PUBLIC_STORAGE_UPLOAD_URL', storageUrl);

    const storageKey = await rl.question(`Enter Storage API Key [${getEnvVal('PUBLIC_STORAGE_API_KEY') || 'bannkd_sec_e4b98d27f10a836c92150d68b9c2409f56e187a2'}]: `) || 'bannkd_sec_e4b98d27f10a836c92150d68b9c2409f56e187a2';
    setEnvVal('PUBLIC_STORAGE_API_KEY', storageKey);

    console.log(`\n--- [4/5] NOCODEBACKEND (NCB) DATABASE ---`);
    const ncbInstance = await rl.question(`Enter NCB Instance Name [${getEnvVal('NCB_INSTANCE') || 'e.g. 43023_myapp_db'}]: `);
    if (ncbInstance) setEnvVal('NCB_INSTANCE', ncbInstance);

    const ncbSecret = await rl.question(`Enter NCB Secret Key [${getEnvVal('NCB_SECRET_KEY') ? '***' : 'sk_live_...'}]: `);
    if (ncbSecret) setEnvVal('NCB_SECRET_KEY', ncbSecret);

    console.log(`\n--- [5/5] STRIPE PAYMENTS & SUBSCRIPTIONS ---`);
    const stripeKey = await rl.question(`Enter Stripe Secret Key [${getEnvVal('STRIPE_SECRET_KEY') ? '***' : 'sk_test_... or sk_live_...'}]: `);
    if (stripeKey) setEnvVal('STRIPE_SECRET_KEY', stripeKey);

    const stripePub = await rl.question(`Enter Stripe Publishable Key [${getEnvVal('STRIPE_PUBLISHABLE_KEY') ? '***' : 'pk_test_... or pk_live_...'}]: `);
    if (stripePub) setEnvVal('STRIPE_PUBLISHABLE_KEY', stripePub);

    const stripeWh = await rl.question(`Enter Stripe Webhook Secret [${getEnvVal('STRIPE_WEBHOOK_SECRET') ? '***' : 'whsec_... (optional)'}]: `);
    if (stripeWh) setEnvVal('STRIPE_WEBHOOK_SECRET', stripeWh);

    // Save .env file
    fs.writeFileSync(envPath, envContent, 'utf-8');
    console.log(`\n✅ Saved configuration to .env successfully!`);
    console.log(`🔒 Note: .env is in .gitignore and will never be committed.`);

    console.log(`\n👉 Next step: Run "npm run check" to verify all credentials and service health.`);
  } finally {
    rl.close();
  }
}

main();
