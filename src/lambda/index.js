import Router from './router.js';
import { healthHandler } from './routes/health.js';
import { sendEmailHandler } from './routes/email.js';
import { uploadStorageHandler } from './routes/storage.js';
import {
  readDataHandler,
  createDataHandler,
  updateDataHandler,
  deleteDataHandler,
} from './routes/data.js';
import {
  createCheckoutHandler,
  createPortalHandler,
  stripeWebhookHandler,
} from './routes/stripe.js';
import {
  getGameHousesHandler,
  submitGuessHandler,
  getGameResultsHandler,
  getLeaderboardHandler,
  getHousePriceSnapshotsHandler,
  updatePlayerProfileHandler,
} from './routes/game.js';

// Initialize router
export const router = new Router();

// Define routes
router.get('/', healthHandler);
router.get('/api/health', healthHandler);

// Ziplow House Price Guessing Game API
router.get('/api/game/houses', getGameHousesHandler);
router.post('/api/game/guess', submitGuessHandler);
router.get('/api/game/results', getGameResultsHandler);
router.get('/api/game/leaderboard', getLeaderboardHandler);
router.get('/api/game/price-history', getHousePriceSnapshotsHandler);
router.post('/api/game/player', updatePlayerProfileHandler);

// Email (AWS SES)
router.post('/api/email/send', sendEmailHandler);

// Storage (MakersHost)
router.post('/api/storage/upload', uploadStorageHandler);

// Database CRUD (NoCodeBackend)
router.get('/api/data/:table', readDataHandler);
router.post('/api/data/:table', createDataHandler);
router.put('/api/data/:table/:id', updateDataHandler);
router.delete('/api/data/:table/:id', deleteDataHandler);

// Stripe Payments & Subscriptions
router.post('/api/stripe/create-checkout-session', createCheckoutHandler);
router.post('/api/stripe/create-portal-session', createPortalHandler);
router.post('/api/stripe/webhook', stripeWebhookHandler);

// Standard CORS headers
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Database-Instance, Cookie',
  'Access-Control-Allow-Credentials': 'true',
};

/**
 * Standard AWS Lambda Handler
 * Supports API Gateway v1/v2 and Lambda Function URLs.
 */
export async function handler(event, context) {
  // Determine HTTP Method
  const method = (
    event.requestContext?.http?.method || 
    event.httpMethod || 
    event.method || 
    'GET'
  ).toUpperCase();

  // Handle CORS preflight OPTIONS request
  if (method === 'OPTIONS') {
    return {
      statusCode: 204,
      headers: CORS_HEADERS,
      body: '',
    };
  }

  // Determine path
  let path = event.rawPath || event.path || '/';
  // Strip stage prefix if API Gateway adds it (e.g. /prod/api -> /api)
  if (event.requestContext?.stage && path.startsWith(`/${event.requestContext.stage}/`)) {
    path = path.slice(event.requestContext.stage.length + 1);
  }

  // Parse body
  let body = event.body;
  if (event.isBase64Encoded && body) {
    body = Buffer.from(body, 'base64').toString('utf-8');
  }

  // Normalize request object
  const req = {
    method,
    path,
    headers: event.headers || {},
    queryStringParameters: event.queryStringParameters || {},
    body,
    rawEvent: event,
    context,
  };

  try {
    const response = await router.handle(req);
    return {
      statusCode: response.statusCode || 200,
      headers: {
        ...CORS_HEADERS,
        ...(response.headers || {}),
      },
      body: response.body || '',
    };
  } catch (err) {
    console.error('Unhandled Lambda Error:', err);
    return {
      statusCode: 500,
      headers: {
        ...CORS_HEADERS,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        error: 'Internal Server Error',
        message: err.message,
      }),
    };
  }
}

export default handler;
