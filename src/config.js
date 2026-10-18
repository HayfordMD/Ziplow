import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');

// Load .env
dotenv.config({ path: path.join(rootDir, '.env') });

export const config = {
  // Domain & GoDaddy
  domain: {
    name: process.env.DOMAIN_NAME || '',
    apiKey: process.env.GODADDY_API_KEY || '',
    apiSecret: process.env.GODADDY_API_SECRET || '',
    env: process.env.GODADDY_ENV || 'prod',
    baseUrl: process.env.GODADDY_ENV === 'ote' 
      ? 'https://api.ote-godaddy.com' 
      : 'https://api.godaddy.com',
  },

  // AWS Configuration
  aws: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID || '',
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY || '',
    region: process.env.AWS_REGION || 'us-east-1',
    ses: {
      fromEmail: process.env.AWS_SES_FROM_EMAIL || '',
      replyTo: process.env.AWS_SES_REPLY_TO || '',
    },
    sns: {
      senderId: process.env.AWS_SNS_SENDER_ID || '',
    },
    lambda: {
      functionName: process.env.AWS_LAMBDA_FUNCTION_NAME || 'backend-serverless-api',
      runtime: process.env.AWS_LAMBDA_RUNTIME || 'nodejs20.x',
      memorySize: parseInt(process.env.AWS_LAMBDA_MEMORY_SIZE || '256', 10),
      timeout: parseInt(process.env.AWS_LAMBDA_TIMEOUT || '30', 10),
      roleArn: process.env.AWS_LAMBDA_ROLE_ARN || '',
    },
  },

  // MakersHost Storage
  storage: {
    provider: process.env.PUBLIC_STORAGE_PROVIDER || 'makershost',
    uploadUrl: process.env.PUBLIC_STORAGE_UPLOAD_URL || 'http://storage.bannkd.com/api/upload.php',
    apiKey: process.env.PUBLIC_STORAGE_API_KEY || 'bannkd_sec_e4b98d27f10a836c92150d68b9c2409f56e187a2',
    defaultFolder: process.env.PUBLIC_STORAGE_DEFAULT_FOLDER || 'backend_uploads',
  },

  // NoCodeBackend
  ncb: {
    instance: process.env.NCB_INSTANCE || '',
    secretKey: process.env.NCB_SECRET_KEY || '',
    authUrl: process.env.NCB_AUTH_API_URL || 'https://app.nocodebackend.com/api/user-auth',
    dataUrl: process.env.NCB_DATA_API_URL || 'https://app.nocodebackend.com/api/data',
    appUrl: process.env.NCB_APP_URL || 'https://app.nocodebackend.com',
  },

  // Stripe Payments & Subscriptions
  stripe: {
    secretKey: process.env.STRIPE_SECRET_KEY || '',
    publishableKey: process.env.STRIPE_PUBLISHABLE_KEY || '',
    webhookSecret: process.env.STRIPE_WEBHOOK_SECRET || '',
    monthlyPriceId: process.env.STRIPE_MONTHLY_PRICE_ID || '',
    yearlyPriceId: process.env.STRIPE_YEARLY_PRICE_ID || '',
    monthlyLink: process.env.STRIPE_MONTHLY_LINK || '',
    yearlyLink: process.env.STRIPE_YEARLY_LINK || '',
    currency: process.env.STRIPE_CURRENCY || 'usd',
  },

  // App & Server
  server: {
    port: parseInt(process.env.PORT || '3000', 10),
    env: process.env.NODE_ENV || 'development',
  },
};

export default config;
