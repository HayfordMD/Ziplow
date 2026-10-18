import { stripeService } from '../../services/stripe.js';

export async function createCheckoutHandler(req) {
  try {
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const { priceId, mode, customerEmail, customerId, successUrl, cancelUrl, metadata } = payload;

    const result = await stripeService.createCheckoutSession({
      priceId,
      mode: mode || 'subscription',
      customerEmail,
      customerId,
      successUrl: successUrl || `https://${req.headers?.host || 'localhost'}/success`,
      cancelUrl: cancelUrl || `https://${req.headers?.host || 'localhost'}/cancel`,
      metadata,
    });

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: err.message }),
    };
  }
}

export async function createPortalHandler(req) {
  try {
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const { customerId, returnUrl } = payload;

    if (!customerId) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ error: 'customerId is required' }),
      };
    }

    const result = await stripeService.createCustomerPortalSession(
      customerId,
      returnUrl || `https://${req.headers?.host || 'localhost'}/account`
    );

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: err.message }),
    };
  }
}

export async function stripeWebhookHandler(req) {
  try {
    const sig = req.headers?.['stripe-signature'] || req.headers?.['Stripe-Signature'];
    const rawBody = req.body || '';

    // Verify webhook signature
    const event = stripeService.constructWebhookEvent(rawBody, sig);

    console.log(`🔔 Stripe Webhook Received: ${event.type} [${event.id}]`);

    // Handle standard Stripe subscription and payment lifecycle events
    switch (event.type) {
      case 'checkout.session.completed': {
        const session = event.data.object;
        console.log(`✅ Checkout completed for customer: ${session.customer || session.customer_email}`);
        // Custom logic: update NCB database, grant user credits, etc.
        break;
      }
      case 'customer.subscription.updated': {
        const subscription = event.data.object;
        console.log(`🔄 Subscription updated: ${subscription.id} (Status: ${subscription.status})`);
        break;
      }
      case 'customer.subscription.deleted': {
        const subscription = event.data.object;
        console.log(`❌ Subscription cancelled: ${subscription.id}`);
        break;
      }
      case 'invoice.payment_succeeded': {
        const invoice = event.data.object;
        console.log(`💰 Payment succeeded for invoice: ${invoice.id}`);
        break;
      }
      case 'invoice.payment_failed': {
        const invoice = event.data.object;
        console.warn(`⚠️ Payment failed for invoice: ${invoice.id}`);
        break;
      }
      default:
        console.log(`Unhandled Stripe event type: ${event.type}`);
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ received: true }),
    };
  } catch (err) {
    console.error(`❌ Webhook Error: ${err.message}`);
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: `Webhook Error: ${err.message}` }),
    };
  }
}
