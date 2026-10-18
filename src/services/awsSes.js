import {
  SESClient,
  VerifyDomainIdentityCommand,
  VerifyDomainDkimCommand,
  GetIdentityVerificationAttributesCommand,
  GetIdentityDkimAttributesCommand,
  VerifyEmailIdentityCommand,
  SendEmailCommand,
  GetSendQuotaCommand,
} from '@aws-sdk/client-ses';
import { SNSClient, PublishCommand } from '@aws-sdk/client-sns';
import config from '../config.js';

/**
 * AWS Simple Email Service (SES) & SNS Service
 * 
 * Provides automated domain verification, DKIM setup, and transactional email sending.
 */
export class AwsEmailService {
  constructor(customConfig = {}) {
    this.region = customConfig.region || config.aws.region;
    this.accessKeyId = customConfig.accessKeyId || config.aws.accessKeyId;
    this.secretAccessKey = customConfig.secretAccessKey || config.aws.secretAccessKey;
    this.fromEmail = customConfig.fromEmail || config.aws.ses.fromEmail;
    this.replyTo = customConfig.replyTo || config.aws.ses.replyTo;

    this.sesClient = null;
    this.snsClient = null;
  }

  getClient() {
    if (!this.sesClient) {
      if (!this.accessKeyId || !this.secretAccessKey) {
        throw new Error(
          'AWS credentials missing. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file.'
        );
      }
      this.sesClient = new SESClient({
        region: this.region,
        credentials: {
          accessKeyId: this.accessKeyId,
          secretAccessKey: this.secretAccessKey,
        },
      });
    }
    return this.sesClient;
  }

  getSnsClient() {
    if (!this.snsClient) {
      if (!this.accessKeyId || !this.secretAccessKey) {
        throw new Error('AWS credentials missing for SNS.');
      }
      this.snsClient = new SNSClient({
        region: this.region,
        credentials: {
          accessKeyId: this.accessKeyId,
          secretAccessKey: this.secretAccessKey,
        },
      });
    }
    return this.snsClient;
  }

  /**
   * Check AWS SES sending quota and account status
   */
  async getQuota() {
    const client = this.getClient();
    const command = new GetSendQuotaCommand({});
    return await client.send(command);
  }

  /**
   * Verify an entire domain in AWS SES
   * Returns verification token (TXT record) and DKIM tokens (CNAME records)
   */
  async verifyDomain(domain) {
    const targetDomain = domain || config.domain.name;
    if (!targetDomain) {
      throw new Error('Domain name required for SES domain verification.');
    }

    const client = this.getClient();

    // 1. Request Domain Identity Verification Token
    const verifyCmd = new VerifyDomainIdentityCommand({ Domain: targetDomain });
    const verifyRes = await client.send(verifyCmd);
    const txtToken = verifyRes.VerificationToken;

    // 2. Request Easy DKIM Tokens (3 CNAME records)
    const dkimCmd = new VerifyDomainDkimCommand({ Domain: targetDomain });
    const dkimRes = await client.send(dkimCmd);
    const dkimTokens = dkimRes.DkimTokens || [];

    return {
      domain: targetDomain,
      verificationToken: txtToken,
      dkimTokens,
      dnsInstructions: {
        txtRecord: {
          type: 'TXT',
          name: `_amazonses.${targetDomain}`,
          value: txtToken,
        },
        dkimRecords: dkimTokens.map(t => ({
          type: 'CNAME',
          name: `${t}._domainkey.${targetDomain}`,
          value: `${t}.dkim.amazonses.com`,
        })),
        spfRecord: {
          type: 'TXT',
          name: targetDomain,
          value: 'v=spf1 include:amazonses.com ~all',
        },
      },
    };
  }

  /**
   * Get the verification and DKIM status for a domain
   */
  async getDomainStatus(domain) {
    const targetDomain = domain || config.domain.name;
    const client = this.getClient();

    const [identRes, dkimRes] = await Promise.all([
      client.send(new GetIdentityVerificationAttributesCommand({ Identities: [targetDomain] })),
      client.send(new GetIdentityDkimAttributesCommand({ Identities: [targetDomain] })),
    ]);

    const identAttrs = identRes.VerificationAttributes?.[targetDomain] || {};
    const dkimAttrs = dkimRes.DkimAttributes?.[targetDomain] || {};

    return {
      domain: targetDomain,
      verificationStatus: identAttrs.VerificationStatus || 'NotFound',
      dkimStatus: dkimAttrs.DkimVerificationStatus || 'NotStarted',
      dkimTokens: dkimAttrs.DkimTokens || [],
    };
  }

  /**
   * Verify an individual sender email address (useful in sandbox mode)
   */
  async verifyEmail(email) {
    const targetEmail = email || this.fromEmail;
    if (!targetEmail) {
      throw new Error('Email address is required for verification.');
    }

    const client = this.getClient();
    const command = new VerifyEmailIdentityCommand({ EmailAddress: targetEmail });
    await client.send(command);

    return {
      success: true,
      email: targetEmail,
      message: `Verification email sent to ${targetEmail}. Click the confirmation link in the email.`,
    };
  }

  /**
   * Send an email via AWS SES
   */
  async sendEmail({ to, subject, html, text, from = this.fromEmail, replyTo = this.replyTo }) {
    if (!to) throw new Error('Recipient email "to" is required.');
    if (!subject) throw new Error('Email "subject" is required.');
    if (!html && !text) throw new Error('Either "html" or "text" body is required.');
    if (!from) throw new Error('Sender email "from" is required. Set AWS_SES_FROM_EMAIL in .env.');

    const client = this.getClient();
    const toAddresses = Array.isArray(to) ? to : [to];

    const body = {};
    if (html) {
      body.Html = { Charset: 'UTF-8', Data: html };
    }
    if (text) {
      body.Text = { Charset: 'UTF-8', Data: text };
    }

    const command = new SendEmailCommand({
      Source: from,
      Destination: {
        ToAddresses: toAddresses,
      },
      Message: {
        Subject: { Charset: 'UTF-8', Data: subject },
        Body: body,
      },
      ReplyToAddresses: replyTo ? [replyTo] : undefined,
    });

    const response = await client.send(command);
    return {
      success: true,
      messageId: response.MessageId,
      to: toAddresses,
      subject,
    };
  }

  /**
   * Send an SMS text message via AWS SNS (optional utility)
   */
  async sendSms({ phoneNumber, message, senderId = config.aws.sns.senderId }) {
    if (!phoneNumber) throw new Error('Recipient phoneNumber is required (E.164 format, e.g. +14155552671).');
    if (!message) throw new Error('SMS message text is required.');

    const client = this.getSnsClient();
    const attributes = {
      'AWS.SNS.SMS.SMSType': {
        DataType: 'String',
        StringValue: 'Transactional',
      },
    };

    if (senderId) {
      attributes['AWS.SNS.SMS.SenderID'] = {
        DataType: 'String',
        StringValue: senderId,
      };
    }

    const command = new PublishCommand({
      PhoneNumber: phoneNumber,
      Message: message,
      MessageAttributes: attributes,
    });

    const res = await client.send(command);
    return {
      success: true,
      messageId: res.MessageId,
      phoneNumber,
    };
  }
}

export const awsEmail = new AwsEmailService();
export default awsEmail;
