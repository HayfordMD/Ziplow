import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, URL } from 'node:url';
import config from '../config.js';
import { handler } from './index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.resolve(__dirname, '../../public');

const PORT = config.server.port || 3000;

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const pathname = parsedUrl.pathname;

  // Serve static files from /public for non-API GET requests
  if (req.method === 'GET' && !pathname.startsWith('/api/')) {
    const relPath = (pathname === '/' || pathname === '/game') ? 'index.html' : pathname.replace(/^\/+/, '');
    const safePath = path.normalize(path.join(publicDir, relPath));
    if (safePath.startsWith(publicDir) && fs.existsSync(safePath) && fs.statSync(safePath).isFile()) {
      const ext = path.extname(safePath).toLowerCase();
      const contentType = MIME_TYPES[ext] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': contentType });
      fs.createReadStream(safePath).pipe(res);
      return;
    }
  }

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
    rawPath: pathname,
    requestContext: {
      http: {
        method: req.method,
        path: pathname,
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
  console.log(`🏡 ZIPLOW PRICE GUESSING GAME & DEV SERVER RUNNING`);
  console.log(`📱 Game Prototype: http://localhost:${PORT}`);
  console.log(`============================================================`);
  console.log(`Available Game & Backend Endpoints:`);
  console.log(`  GET    http://localhost:${PORT}/                  (Mobile Game Prototype)`);
  console.log(`  GET    http://localhost:${PORT}/api/game/houses   (Playable Houses + Carousel Photos)`);
  console.log(`  POST   http://localhost:${PORT}/api/game/guess    (Submit Guess -> Results & Leaderboard)`);
  console.log(`  GET    http://localhost:${PORT}/api/game/results  (Last Results: House vs Guess vs Off)`);
  console.log(`  GET    http://localhost:${PORT}/api/game/leaderboard (Leaderboards)`);
  console.log(`  GET    http://localhost:${PORT}/api/game/price-history (Date-Based House Price Snapshots)`);
  console.log(`  GET    http://localhost:${PORT}/api/health`);
  console.log(`============================================================\n`);
});
