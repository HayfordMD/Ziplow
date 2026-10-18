import http from 'node:http';
import { URL } from 'node:url';
import config from '../config.js';
import { handler } from './index.js';

const PORT = config.server.port || 3000;

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  // Collect request body
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  const rawBody = Buffer.concat(chunks).toString('utf-8');

  // Convert Node HTTP request to Lambda Function URL / API Gateway format
  const queryParams = {};
  parsedUrl.searchParams.forEach((value, key) => {
    queryParams[key] = value;
  });

  const event = {
    rawPath: parsedUrl.pathname,
    requestContext: {
      http: {
        method: req.method,
        path: parsedUrl.pathname,
      },
    },
    headers: req.headers,
    queryStringParameters: Object.keys(queryParams).length > 0 ? queryParams : null,
    body: rawBody,
    isBase64Encoded: false,
  };

  try {
    const result = await handler(event, {});

    res.writeHead(result.statusCode || 200, result.headers || {});
    res.end(result.body || '');
  } catch (err) {
    console.error('Local Server Error:', err);
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: err.message }));
  }
});

server.listen(PORT, () => {
  console.log(`\n============================================================`);
  console.log(`🚀 LOCAL LAMBDA DEV SERVER RUNNING`);
  console.log(`📡 URL: http://localhost:${PORT}`);
  console.log(`============================================================`);
  console.log(`Available Endpoints:`);
  console.log(`  GET    http://localhost:${PORT}/api/health`);
  console.log(`  POST   http://localhost:${PORT}/api/email/send`);
  console.log(`  POST   http://localhost:${PORT}/api/storage/upload`);
  console.log(`  GET    http://localhost:${PORT}/api/data/:table`);
  console.log(`  POST   http://localhost:${PORT}/api/data/:table`);
  console.log(`  PUT    http://localhost:${PORT}/api/data/:table/:id`);
  console.log(`  DELETE http://localhost:${PORT}/api/data/:table/:id`);
  console.log(`============================================================\n`);
});
