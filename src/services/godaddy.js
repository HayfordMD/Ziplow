import config from '../config.js';

/**
 * GoDaddy DNS API Service
 * 
 * GoDaddy allows programmatic management of DNS records after domain purchase.
 * API keys are obtained at: https://developer.godaddy.com/keys
 * 
 * Documentation: https://developer.godaddy.com/doc/endpoint/domains
 */
export class GoDaddyService {
  constructor(customConfig = {}) {
    this.apiKey = customConfig.apiKey || config.domain.apiKey;
    this.apiSecret = customConfig.apiSecret || config.domain.apiSecret;
    this.domain = customConfig.domain || config.domain.name;
    this.baseUrl = customConfig.baseUrl || config.domain.baseUrl;
  }

  getHeaders() {
    if (!this.apiKey) {
      throw new Error(
        'GoDaddy API credential missing. Set GODADDY_API_KEY in your .env file.\n' +
        'Generate your Personal Access Token at: https://developer.godaddy.com/keys'
      );
    }

    // New GoDaddy Personal Access Token (starts with gd... or no secret provided)
    let authHeader;
    if (this.apiKey.startsWith('gd') || !this.apiSecret) {
      authHeader = `Bearer ${this.apiKey}`;
    } else {
      // Classic GoDaddy sso-key format
      authHeader = `sso-key ${this.apiKey}:${this.apiSecret}`;
    }

    return {
      'Authorization': authHeader,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
  }

  /**
   * Check if domain exists in the GoDaddy account and verify credentials
   */
  async verifyDomain(domain = this.domain) {
    if (!domain) throw new Error('Domain name is required.');
    const url = `${this.baseUrl}/v1/domains/${domain}`;
    const res = await fetch(url, { headers: this.getHeaders() });
    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`GoDaddy API Error (${res.status}): ${errorText}`);
    }
    return await res.json();
  }

  /**
   * Fetch all DNS records for the domain
   */
  async getRecords(domain = this.domain, type = null) {
    if (!domain) throw new Error('Domain name is required.');
    const url = type 
      ? `${this.baseUrl}/v1/domains/${domain}/records/${type}`
      : `${this.baseUrl}/v1/domains/${domain}/records`;
    
    const res = await fetch(url, { headers: this.getHeaders() });
    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Failed to fetch GoDaddy DNS records (${res.status}): ${errorText}`);
    }
    return await res.json();
  }

  /**
   * Add or update specific DNS records (PATCH /v1/domains/{domain}/records)
   * 
   * @param {Array<{ type: string, name: string, data: string, ttl?: number, priority?: number }>} records
   */
  async patchRecords(records, domain = this.domain) {
    if (!domain) throw new Error('Domain name is required.');
    if (!Array.isArray(records) || records.length === 0) {
      throw new Error('Records must be a non-empty array.');
    }

    const formattedRecords = records.map(r => ({
      type: r.type.toUpperCase(),
      name: r.name,
      data: r.data,
      ttl: r.ttl || 3600,
      ...(r.priority !== undefined ? { priority: r.priority } : {}),
    }));

    const url = `${this.baseUrl}/v1/domains/${domain}/records`;
    const res = await fetch(url, {
      method: 'PATCH',
      headers: this.getHeaders(),
      body: JSON.stringify(formattedRecords),
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Failed to patch GoDaddy DNS records (${res.status}): ${errorText}`);
    }

    return { success: true, count: formattedRecords.length };
  }

  /**
   * Set or overwrite all records of a specific type and name
   * (PUT /v1/domains/{domain}/records/{type}/{name})
   */
  async setRecord(type, name, data, ttl = 3600, domain = this.domain) {
    if (!domain) throw new Error('Domain name is required.');
    const url = `${this.baseUrl}/v1/domains/${domain}/records/${type.toUpperCase()}/${name}`;
    const payload = [
      {
        data: String(data),
        ttl: Number(ttl),
      },
    ];

    const res = await fetch(url, {
      method: 'PUT',
      headers: this.getHeaders(),
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Failed to set ${type} record for ${name} (${res.status}): ${errorText}`);
    }

    return { success: true, type, name, data };
  }

  /**
   * Point the root domain or subdomain to an IP address (A record)
   */
  async pointToIp(name, ipAddress, domain = this.domain) {
    return await this.setRecord('A', name, ipAddress, 600, domain);
  }

  /**
   * Point a subdomain (e.g. 'api' or 'www') to a target host (CNAME record)
   */
  async pointToHost(name, targetHost, domain = this.domain) {
    return await this.setRecord('CNAME', name, targetHost, 3600, domain);
  }

  /**
   * Sync AWS SES verification records to GoDaddy automatically:
   * 1. TXT record: _amazonses.<domain> -> token
   * 2. DKIM CNAME records: <token>._domainkey.<domain> -> <token>.dkim.amazonses.com
   * 3. SPF TXT record: @ -> "v=spf1 include:amazonses.com ~all"
   */
  async syncSesDnsRecords({ txtRecord, dkimTokens = [], enableSpf = true }, domain = this.domain) {
    const recordsToPatch = [];

    if (txtRecord) {
      recordsToPatch.push({
        type: 'TXT',
        name: '_amazonses',
        data: txtRecord,
        ttl: 3600,
      });
    }

    if (dkimTokens && dkimTokens.length > 0) {
      for (const token of dkimTokens) {
        recordsToPatch.push({
          type: 'CNAME',
          name: `${token}._domainkey`,
          data: `${token}.dkim.amazonses.com`,
          ttl: 3600,
        });
      }
    }

    if (enableSpf) {
      recordsToPatch.push({
        type: 'TXT',
        name: '@',
        data: 'v=spf1 include:amazonses.com ~all',
        ttl: 3600,
      });
    }

    if (recordsToPatch.length === 0) {
      return { success: true, message: 'No records to sync' };
    }

    return await this.patchRecords(recordsToPatch, domain);
  }
}

export const godaddy = new GoDaddyService();
export default godaddy;
