import { awsEmail } from '../../services/awsSes.js';

export async function sendEmailHandler(req) {
  try {
    const payload = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
    const { to, subject, html, text, from } = payload;

    if (!to || !subject || (!html && !text)) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error: 'Missing required parameters: "to", "subject", and ("html" or "text") are required.',
        }),
      };
    }

    const result = await awsEmail.sendEmail({ to, subject, html, text, from });

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
