import { makersHost } from '../../services/makershost.js';

export async function uploadStorageHandler(req) {
  try {
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const { fileBase64, filename, folder, mimeType, name } = payload;

    if (!fileBase64 || !filename) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error: 'Missing required parameters: "fileBase64" and "filename" are required.',
        }),
      };
    }

    const buffer = Buffer.from(fileBase64, 'base64');
    const result = await makersHost.upload(buffer, {
      filename,
      folder,
      mimeType: mimeType || 'application/octet-stream',
      name,
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
