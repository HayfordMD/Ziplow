#!/usr/bin/env node
import { awsEmail } from '../src/services/awsSes.js';
import { godaddy } from '../src/services/godaddy.js';
import config from '../src/config.js';

const syncGodaddy = process.argv.includes('--sync-godaddy') || process.argv.includes('-s');

async function main() {
  const domain = config.domain.name;
  if (!domain) {
    console.error('❌ Error: DOMAIN_NAME is not set in .env');
    process.exit(1);
  }

  console.log(`\n============================================================`);
  console.log(`📧 AWS SES Domain Verification & DKIM Setup`);
  console.log(`📌 Domain:  ${domain}`);
  console.log(`🌍 Region:  ${config.aws.region}`);
  console.log(`============================================================\n`);

  try {
    // 1. Check existing status first
    console.log(`🔍 Checking current SES verification status...`);
    const status = await awsEmail.getDomainStatus(domain);
    console.log(`   Verification Status: ${status.verificationStatus}`);
    console.log(`   DKIM Status:         ${status.dkimStatus}\n`);

    // 2. Initiate domain verification & fetch DKIM
    console.log(`🚀 Requesting AWS SES verification & DKIM tokens...`);
    const sesInfo = await awsEmail.verifyDomain(domain);

    console.log(`\n📋 Required DNS Records for ${domain}:`);
    console.log(`------------------------------------------------------------`);
    console.log(`1. SES Verification TXT Record:`);
    console.log(`   Type:  ${sesInfo.dnsInstructions.txtRecord.type}`);
    console.log(`   Name:  ${sesInfo.dnsInstructions.txtRecord.name}`);
    console.log(`   Value: ${sesInfo.dnsInstructions.txtRecord.value}`);

    console.log(`\n2. DKIM CNAME Records (3 tokens):`);
    sesInfo.dnsInstructions.dkimRecords.forEach((rec, idx) => {
      console.log(`   [${idx + 1}] Name:  ${rec.name}`);
      console.log(`       Value: ${rec.value}`);
    });

    console.log(`\n3. SPF TXT Record:`);
    console.log(`   Type:  ${sesInfo.dnsInstructions.spfRecord.type}`);
    console.log(`   Name:  ${sesInfo.dnsInstructions.spfRecord.name}`);
    console.log(`   Value: ${sesInfo.dnsInstructions.spfRecord.value}`);
    console.log(`------------------------------------------------------------\n`);

    // 3. Auto-sync to GoDaddy if enabled or requested
    if (syncGodaddy || config.domain.apiKey) {
      console.log(`⚡ GoDaddy credentials detected. Pushing DNS records to GoDaddy automatically...`);
      try {
        await godaddy.syncSesDnsRecords({
          txtRecord: sesInfo.verificationToken,
          dkimTokens: sesInfo.dkimTokens,
          enableSpf: true,
        }, domain);

        console.log(`✅ All SES verification, DKIM, and SPF records pushed to GoDaddy successfully!`);
        console.log(`🕒 DNS propagation usually takes 5-15 minutes.`);
      } catch (gdErr) {
        console.warn(`⚠️ GoDaddy automatic sync skipped/failed: ${gdErr.message}`);
        console.log(`You can manually add the records above to your DNS provider.`);
      }
    } else {
      console.log(`💡 Tip: To automatically add these records to GoDaddy DNS:`);
      console.log(`   Set GODADDY_API_KEY and GODADDY_API_SECRET in .env and run:`);
      console.log(`   npm run dns:sync-ses\n`);
    }

    // 4. Verify sender email identity as well
    if (config.aws.ses.fromEmail) {
      console.log(`📨 Sender email address: ${config.aws.ses.fromEmail}`);
      console.log(`   Verifying email identity in SES...`);
      try {
        await awsEmail.verifyEmail(config.aws.ses.fromEmail);
        console.log(`✅ Verification requested for ${config.aws.ses.fromEmail}`);
      } catch (emErr) {
        console.log(`   Note: ${emErr.message}`);
      }
    }

    console.log(`\n🎉 AWS SES Setup Complete!`);
  } catch (err) {
    console.error(`\n❌ AWS SES Setup Error:`, err.message);
    process.exit(1);
  }
}

main();
