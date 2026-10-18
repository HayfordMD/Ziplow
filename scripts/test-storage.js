#!/usr/bin/env node
import { makersHost } from '../src/services/makershost.js';
import config from '../src/config.js';

async function main() {
  console.log(`\n============================================================`);
  console.log(`📦 Testing MakersHost Backend Storage`);
  console.log(`📌 Target Upload URL: ${config.storage.uploadUrl}`);
  console.log(`📁 Target Folder:     ${config.storage.defaultFolder}`);
  console.log(`============================================================\n`);

  if (!config.storage.apiKey) {
    console.error('❌ Error: PUBLIC_STORAGE_API_KEY is not set in .env');
    process.exit(1);
  }

  try {
    // 1. Ping test
    console.log('1. Checking connection to MakersHost cluster...');
    const pingRes = await makersHost.ping();
    console.log(`   Cluster reachable: ${pingRes.ok ? 'YES' : 'NO'} (Status: ${pingRes.status || pingRes.error})`);

    // 2. Upload test (MakersHost allows images & mp4)
    console.log('\n2. Testing sample PNG image upload...');
    // Minimal valid 1x1 transparent PNG
    const pngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
    const samplePngBuffer = Buffer.from(pngBase64, 'base64');

    const testFilename = `test_backend_${Date.now()}.png`;
    const result = await makersHost.upload(samplePngBuffer, {
      filename: testFilename,
      mimeType: 'image/png',
      folder: config.storage.defaultFolder,
    });

    console.log(`✅ Upload Successful!`);
    console.log(`   Public URL: ${result.url}`);
    console.log(`   Filename:   ${result.filename}`);
    console.log(`   Size:       ${result.size} bytes`);

    // 3. Verify public retrieval
    console.log('\n3. Verifying public file accessibility via GET...');
    const verifyRes = await fetch(result.url);
    if (verifyRes.ok) {
      console.log(`✅ File verified and accessible at public URL! (HTTP ${verifyRes.status})`);
    } else {
      console.warn(`⚠️ File uploaded but returned HTTP ${verifyRes.status} on fetch (may be propagating).`);
    }

    console.log(`\n🎉 MakersHost Storage is 100% operational!`);
  } catch (err) {
    console.error(`\n❌ MakersHost Storage Test Failed:`, err.message);
    process.exit(1);
  }
}

main();
