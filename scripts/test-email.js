#!/usr/bin/env node
import { awsEmail } from '../src/services/awsSes.js';
import config from '../src/config.js';

const [,, recipientEmail, customSubject] = process.argv;

async function main() {
  console.log(`\n============================================================`);
  console.log(`📧 AWS SES Email Test Utility`);
  console.log(`📌 Sender:    ${config.aws.ses.fromEmail || '(NOT SET)'}`);
  console.log(`🌍 Region:    ${config.aws.region}`);
  console.log(`============================================================\n`);

  if (!config.aws.accessKeyId || !config.aws.secretAccessKey) {
    console.error(`❌ AWS Credentials missing. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env`);
    process.exit(1);
  }

  if (!config.aws.ses.fromEmail) {
    console.error(`❌ Sender email missing. Set AWS_SES_FROM_EMAIL in .env`);
    process.exit(1);
  }

  const to = recipientEmail || config.aws.ses.fromEmail;
  const subject = customSubject || `Test Email from Backend-Build [${new Date().toLocaleTimeString()}]`;

  console.log(`1. Checking SES sending quota...`);
  try {
    const quota = await awsEmail.getQuota();
    console.log(`   Max 24h Send:     ${quota.Max24HourSend}`);
    console.log(`   Sent Last 24h:    ${quota.SentLast24Hours}`);
    console.log(`   Max Send Rate:    ${quota.MaxSendRate}/sec`);
  } catch (qErr) {
    console.warn(`   Note: Could not retrieve quota (${qErr.message})`);
  }

  console.log(`\n2. Sending test email...`);
  console.log(`   From: ${config.aws.ses.fromEmail}`);
  console.log(`   To:   ${to}`);
  console.log(`   Subj: ${subject}`);

  try {
    const res = await awsEmail.sendEmail({
      to,
      subject,
      html: `
        <div style="font-family: Arial, sans-serif; padding: 20px; background: #f9f9f9; color: #333;">
          <h2 style="color: #2563eb;">🚀 AWS SES Test Email</h2>
          <p>This email was successfully sent by your <strong>backend-build</strong> deployment boilerplate!</p>
          <hr style="border: 0; border-top: 1px solid #ddd; margin: 20px 0;" />
          <ul>
            <li><strong>Timestamp:</strong> ${new Date().toISOString()}</li>
            <li><strong>Region:</strong> ${config.aws.region}</li>
            <li><strong>Sender:</strong> ${config.aws.ses.fromEmail}</li>
          </ul>
          <p style="color: #888; font-size: 12px;">Sent from Backend Serverless Architecture</p>
        </div>
      `,
      text: `AWS SES Test Email\n\nThis is a test email sent from your backend-build deployment system at ${new Date().toISOString()}.`,
    });

    console.log(`\n✅ Email Sent Successfully!`);
    console.log(`   Message ID: ${res.messageId}`);
    console.log(`\n🎉 AWS SES is fully functional!`);
  } catch (err) {
    console.error(`\n❌ Email Send Failed:`, err.message);
    if (err.message.includes('MessageRejected') || err.message.includes('Email address is not verified')) {
      console.error('\nTip: AWS SES Sandbox requires verifying both sender and recipient emails.');
      console.error('Run: node scripts/aws-ses-setup.js to verify identities or move out of sandbox.');
    }
    process.exit(1);
  }
}

main();
