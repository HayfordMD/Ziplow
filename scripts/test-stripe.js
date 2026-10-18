#!/usr/bin/env node
import { stripeService } from '../src/services/stripe.js';
import config from '../src/config.js';

async function main() {
  console.log(`\n============================================================`);
  console.log(`💳 Stripe Payments & Subscription Verification`);
  console.log(`📌 Currency: ${config.stripe.currency.toUpperCase()}`);
  console.log(`============================================================\n`);

  if (!config.stripe.secretKey) {
    console.error(`❌ STRIPE_SECRET_KEY is not set in your .env file.`);
    console.log(`To configure Stripe:`);
    console.log(`1. Visit https://dashboard.stripe.com/apikeys`);
    console.log(`2. Copy your Secret key (sk_test_... or sk_live_...)`);
    console.log(`3. Add to .env:`);
    console.log(`   STRIPE_SECRET_KEY=sk_test_...`);
    console.log(`   STRIPE_PUBLISHABLE_KEY=pk_test_...`);
    console.log(`   STRIPE_WEBHOOK_SECRET=whsec_...`);
    process.exit(1);
  }

  try {
    console.log(`1. Verifying Stripe API Key...`);
    const conn = await stripeService.testConnection();

    if (!conn.ok) {
      throw new Error(conn.error || 'Connection failed');
    }

    console.log(`   ✅ Connected to Stripe!`);
    console.log(`   Mode:     ${conn.mode.toUpperCase()}`);
    console.log(`   Livemode: ${conn.livemode ? 'YES' : 'NO'}`);

    console.log(`\n2. Inspecting configured Price IDs & Payment Links...`);
    console.log(`   Monthly Price ID: ${config.stripe.monthlyPriceId || '(Not set)'}`);
    console.log(`   Yearly Price ID:  ${config.stripe.yearlyPriceId || '(Not set)'}`);
    console.log(`   Monthly Link:     ${config.stripe.monthlyLink || '(Not set)'}`);
    console.log(`   Yearly Link:      ${config.stripe.yearlyLink || '(Not set)'}`);
    console.log(`   Webhook Secret:   ${config.stripe.webhookSecret ? 'Configured (whsec_...)' : '(Not set - needed for webhooks)'}`);

    console.log(`\n3. Fetching active Products & Prices from your Stripe account...`);
    const prices = await stripeService.listPrices();
    if (prices.length > 0) {
      console.table(prices);
    } else {
      console.log(`   No active products found yet. Create them in https://dashboard.stripe.com/products`);
    }

    console.log(`\n🎉 Stripe integration is fully operational!`);
  } catch (err) {
    console.error(`\n❌ Stripe Verification Failed:`, err.message);
    process.exit(1);
  }
}

main();
