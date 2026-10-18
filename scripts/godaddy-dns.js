#!/usr/bin/env node
import { godaddy } from '../src/services/godaddy.js';
import config from '../src/config.js';

const [,, command, ...args] = process.argv;

async function main() {
  const domain = config.domain.name;

  if (!domain) {
    console.error('❌ Error: DOMAIN_NAME is not set in .env');
    process.exit(1);
  }

  console.log(`\n============================================================`);
  console.log(`🌐 GoDaddy DNS Automation Tool`);
  console.log(`📌 Target Domain: ${domain}`);
  console.log(`⚡ Environment:   ${config.domain.env} (${config.domain.baseUrl})`);
  console.log(`============================================================\n`);

  try {
    switch (command) {
      case 'list': {
        console.log(`🔍 Fetching current DNS records for ${domain}...`);
        const records = await godaddy.getRecords();
        console.table(
          records.map(r => ({
            Type: r.type,
            Name: r.name,
            Data: r.data.length > 50 ? r.data.slice(0, 47) + '...' : r.data,
            TTL: r.ttl,
            Priority: r.priority || '-',
          }))
        );
        break;
      }

      case 'set-a': {
        const [name, ip] = args;
        if (!name || !ip) {
          console.error('Usage: node scripts/godaddy-dns.js set-a <name> <ip>');
          console.error('Example: node scripts/godaddy-dns.js set-a @ 159.203.1.20');
          process.exit(1);
        }
        console.log(`🚀 Setting A record: "${name}" -> ${ip}...`);
        await godaddy.pointToIp(name, ip);
        console.log(`✅ A record successfully updated!`);
        break;
      }

      case 'set-cname': {
        const [name, target] = args;
        if (!name || !target) {
          console.error('Usage: node scripts/godaddy-dns.js set-cname <name> <targetHost>');
          console.error('Example: node scripts/godaddy-dns.js set-cname api my-lambda.lambda-url.us-east-1.on.aws');
          process.exit(1);
        }
        console.log(`🚀 Setting CNAME record: "${name}" -> ${target}...`);
        await godaddy.pointToHost(name, target);
        console.log(`✅ CNAME record successfully updated!`);
        break;
      }

      case 'set-txt': {
        const [name, txtValue] = args;
        if (!name || !txtValue) {
          console.error('Usage: node scripts/godaddy-dns.js set-txt <name> <value>');
          process.exit(1);
        }
        console.log(`🚀 Setting TXT record: "${name}" -> "${txtValue}"...`);
        await godaddy.setRecord('TXT', name, txtValue);
        console.log(`✅ TXT record successfully updated!`);
        break;
      }

      case 'verify':
      default: {
        console.log(`🔍 Verifying GoDaddy credentials & domain ownership...`);
        const details = await godaddy.verifyDomain();
        console.log(`✅ Domain verified successfully!`);
        console.log(`   Domain ID:    ${details.domainId || 'N/A'}`);
        console.log(`   Status:       ${details.status || 'ACTIVE'}`);
        console.log(`   Expires:      ${details.expires || 'N/A'}`);
        console.log(`\nAvailable commands:`);
        console.log(`  node scripts/godaddy-dns.js list`);
        console.log(`  node scripts/godaddy-dns.js set-a <name> <ip>`);
        console.log(`  node scripts/godaddy-dns.js set-cname <name> <targetHost>`);
        console.log(`  node scripts/godaddy-dns.js set-txt <name> <value>`);
        break;
      }
    }
  } catch (err) {
    console.error(`\n❌ GoDaddy DNS Operation Failed:`, err.message);
    if (err.message.includes('401') || err.message.includes('403')) {
      console.error('\nTip: Check that GODADDY_API_KEY and GODADDY_API_SECRET are correct.');
      console.error('Ensure you created a "Production" key at https://developer.godaddy.com/keys');
    }
    process.exit(1);
  }
}

main();
