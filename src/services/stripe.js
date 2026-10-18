import Stripe from 'stripe';
import config from '../config.js';

/**
 * Stripe Payments & Subscription Service
 * 
 * Handles Checkout Sessions, Customer Portals, Webhook verification, and Subscriptions.
 */
export class StripeService {
  constructor(customConfig = {}) {
    this.secretKey = customConfig.secretKey || config.stripe.secretKey;
    this.publishableKey = customConfig.publishableKey || config.stripe.publishableKey;
    this.webhookSecret = customConfig.webhookSecret || config.stripe.webhookSecret;
    this.monthlyPriceId = customConfig.monthlyPriceId || config.stripe.monthlyPriceId;
    this.yearlyPriceId = customConfig.yearlyPriceId || config.stripe.yearlyPriceId;
    this.currency = customConfig.currency || config.stripe.currency || 'usd';

    this.stripeInstance = null;
  }

  getClient() {
    if (!this.stripeInstance) {
      if (!this.secretKey) {
        throw new Error(
          'Stripe Secret Key missing. Set STRIPE_SECRET_KEY in your .env file.'
        );
      }
      this.stripeInstance = new Stripe(this.secretKey);
    }
    return this.stripeInstance;
  }

  /**
   * Test connection to Stripe API and check account mode (test vs live)
   */
  async testConnection() {
    if (!this.secretKey) {
      return { ok: false, message: 'STRIPE_SECRET_KEY is missing.' };
    }

    try {
      const stripe = this.getClient();
      const balance = await stripe.balance.retrieve();
      const isLive = !this.secretKey.startsWith('sk_test_');

      return {
        ok: true,
        mode: isLive ? 'live' : 'test',
        livemode: balance.livemode,
        currency: this.currency,
      };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }

  /**
   * Create a Stripe Checkout Session (Subscription or One-time Payment)
   * 
   * @param {Object} options
   * @param {string} [options.priceId] - Stripe Price ID (e.g. price_1N...)
   * @param {'subscription'|'payment'} [options.mode='subscription']
   * @param {string} [options.customerEmail] - Pre-fill email
   * @param {string} [options.customerId] - Existing Stripe Customer ID
   * @param {string} options.successUrl - Redirect URL after successful payment
   * @param {string} options.cancelUrl - Redirect URL if payment cancelled
   * @param {Object} [options.metadata] - Custom metadata (e.g. userId, ncbId)
   */
  async createCheckoutSession(options = {}) {
    const stripe = this.getClient();
    const {
      priceId = this.monthlyPriceId,
      mode = 'subscription',
      customerEmail,
      customerId,
      successUrl,
      cancelUrl,
      metadata = {},
    } = options;

    if (!priceId) {
      throw new Error('Price ID is required to create a Checkout Session. Set STRIPE_MONTHLY_PRICE_ID or pass priceId.');
    }
    if (!successUrl || !cancelUrl) {
      throw new Error('Both successUrl and cancelUrl are required for Checkout Session.');
    }

    const sessionParams = {
      mode,
      payment_method_types: ['card'],
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      success_url: successUrl,
      cancel_url: cancelUrl,
      metadata,
    };

    if (customerId) {
      sessionParams.customer = customerId;
    } else if (customerEmail) {
      sessionParams.customer_email = customerEmail;
    }

    // Allow promotion codes
    sessionParams.allow_promotion_codes = true;

    const session = await stripe.checkout.sessions.create(sessionParams);
    return {
      sessionId: session.id,
      url: session.url,
    };
  }

  /**
   * Create a Stripe Customer Portal session for billing management
   */
  async createCustomerPortalSession(customerId, returnUrl) {
    if (!customerId) {
      throw new Error('customerId is required to create a Customer Portal session.');
    }
    if (!returnUrl) {
      throw new Error('returnUrl is required.');
    }

    const stripe = this.getClient();
    const session = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: returnUrl,
    });

    return {
      url: session.url,
    };
  }

  /**
   * Verify and construct a Stripe Webhook Event
   * 
   * @param {string|Buffer} rawBody - The unparsed raw HTTP request body
   * @param {string} signatureHeader - The 'stripe-signature' HTTP header
   */
  constructWebhookEvent(rawBody, signatureHeader) {
    if (!this.webhookSecret) {
      throw new Error('STRIPE_WEBHOOK_SECRET is missing. Set it in .env to verify webhooks.');
    }
    if (!signatureHeader) {
      throw new Error('Missing stripe-signature header.');
    }

    const stripe = this.getClient();
    return stripe.webhooks.constructEvent(rawBody, signatureHeader, this.webhookSecret);
  }

  /**
   * Fetch active products and prices
   */
  async listPrices() {
    const stripe = this.getClient();
    const prices = await stripe.prices.list({ active: true, expand: ['data.product'] });
    return prices.data.map(p => ({
      id: p.id,
      product: p.product?.name || p.product,
      amount: p.unit_amount ? (p.unit_amount / 100) : 0,
      currency: p.currency,
      interval: p.recurring?.interval || 'one-time',
    }));
  }
}

export const stripeService = new StripeService();
export default stripeService;
