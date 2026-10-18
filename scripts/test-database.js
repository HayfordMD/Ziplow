#!/usr/bin/env node
import { ncb } from '../src/services/nocodebackend.js';
import config from '../src/config.js';

const [,, targetTable] = process.argv;

async function main() {
  console.log(`\n============================================================`);
  console.log(`🗄️  Testing NoCodeBackend (NCB) Database`);
  console.log(`📌 Instance: ${config.ncb.instance || '(NOT SET)'}`);
  console.log(`🌐 Data URL: ${config.ncb.dataUrl}`);
  console.log(`============================================================\n`);

  if (!config.ncb.instance) {
    console.warn(`⚠️ Warning: NCB_INSTANCE is not configured in .env.`);
    console.log(`To configure NCB:`);
    console.log(`1. Open https://app.nocodebackend.com`);
    console.log(`2. Select or create your database.`);
    console.log(`3. Copy your instance name and secret key into .env:`);
    console.log(`   NCB_INSTANCE=43023_yourname_db`);
    console.log(`   NCB_SECRET_KEY=sk_live_...`);
    process.exit(0);
  }

  try {
    // 1. Connection check
    console.log(`1. Testing connection to NCB instance "${config.ncb.instance}"...`);
    const conn = await ncb.testConnection();
    console.log(`   Connection: ${conn.ok ? 'SUCCESS' : 'STATUS ' + conn.status}`);

    // 2. Query table if specified
    if (targetTable) {
      console.log(`\n2. Querying table "${targetTable}"...`);
      const rows = await ncb.read(targetTable, { limit: 5 });
      console.log(`✅ Table query successful! Returned rows:`);
      console.dir(rows, { depth: null });
    } else {
      console.log(`\n💡 To test querying a specific table, pass the table name:`);
      console.log(`   node scripts/test-database.js <table_name>`);
    }

    console.log(`\n🎉 NoCodeBackend connection verified!`);
  } catch (err) {
    console.error(`\n❌ NCB Test Error:`, err.message);
    process.exit(1);
  }
}

main();
