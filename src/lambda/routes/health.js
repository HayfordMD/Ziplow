import config from '../../config.js';

export async function healthHandler(req) {
  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      status: 'healthy',
      timestamp: new Date().toISOString(),
      service: 'Backend-Build Serverless Boilerplate',
      version: '1.0.0',
      configured: {
        domain: Boolean(config.domain.name),
        godaddyDns: Boolean(config.domain.apiKey && config.domain.apiSecret),
        awsSes: Boolean(config.aws.accessKeyId && config.aws.secretAccessKey && config.aws.ses.fromEmail),
        makersHost: Boolean(config.storage.apiKey),
        nocodeBackend: Boolean(config.ncb.instance && config.ncb.secretKey),
        stripe: Boolean(config.stripe.secretKey),
      },
    }),
  };
}
