import { ncb } from '../../services/nocodebackend.js';

function extractSessionToken(headers = {}) {
  const cookieHeader = headers.cookie || headers.Cookie || '';
  const match = cookieHeader.match(/better-auth\.session_token=([^;]+)/);
  if (match) return match[1];

  const auth = headers.authorization || headers.Authorization || '';
  if (auth.startsWith('Bearer ')) {
    return auth.slice(7).trim();
  }
  return null;
}

export async function readDataHandler(req) {
  try {
    const table = req.params?.table;
    const sessionToken = extractSessionToken(req.headers);
    const query = req.queryStringParameters || {};

    const result = await ncb.read(table, {
      id: query.id,
      ownerId: query.owner_id,
      limit: query.limit ? parseInt(query.limit, 10) : undefined,
      offset: query.offset ? parseInt(query.offset, 10) : undefined,
      sessionToken,
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

export async function createDataHandler(req) {
  try {
    const table = req.params?.table;
    const sessionToken = extractSessionToken(req.headers);
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const query = req.queryStringParameters || {};

    const result = await ncb.create(table, payload, {
      ownerId: query.owner_id,
      sessionToken,
    });

    return {
      statusCode: 201,
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

export async function updateDataHandler(req) {
  try {
    const table = req.params?.table;
    const id = req.params?.id || req.queryStringParameters?.id;
    const sessionToken = extractSessionToken(req.headers);
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});

    const result = await ncb.update(table, id, payload, { sessionToken });

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

export async function deleteDataHandler(req) {
  try {
    const table = req.params?.table;
    const id = req.params?.id || req.queryStringParameters?.id;
    const sessionToken = extractSessionToken(req.headers);

    const result = await ncb.delete(table, id, { sessionToken });

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
