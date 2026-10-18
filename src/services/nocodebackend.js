import config from '../config.js';

/**
 * NoCodeBackend (NCB) Database & CRUD Service
 * 
 * Provides server-side proxy operations and CRUD helper methods
 * using NCB_SECRET_KEY for authenticated access.
 */
export class NoCodeBackendService {
  constructor(customConfig = {}) {
    this.instance = customConfig.instance || config.ncb.instance;
    this.secretKey = customConfig.secretKey || config.ncb.secretKey;
    this.dataUrl = customConfig.dataUrl || config.ncb.dataUrl;
    this.authUrl = customConfig.authUrl || config.ncb.authUrl;
    this.appUrl = customConfig.appUrl || config.ncb.appUrl;
  }

  getHeaders(sessionToken = null) {
    const headers = {
      'Content-Type': 'application/json',
      'X-Database-Instance': this.instance,
    };

    if (this.secretKey) {
      headers['Authorization'] = `Bearer ${this.secretKey}`;
    }

    if (sessionToken) {
      headers['Cookie'] = `better-auth.session_token=${sessionToken}`;
    }

    return headers;
  }

  /**
   * Read records from a table
   * 
   * @param {string} table - Table name (e.g. 'contacts', 'posts')
   * @param {Object} [options]
   * @param {string} [options.id] - Specific record ID
   * @param {string} [options.ownerId] - Owner ID for scoped public reads
   * @param {number} [options.limit] - Max records
   * @param {number} [options.offset] - Pagination offset
   * @param {string} [options.sessionToken] - User session token
   */
  async read(table, options = {}) {
    if (!this.instance) throw new Error('NCB_INSTANCE is not configured in .env.');
    
    const params = new URLSearchParams({
      instance: this.instance,
    });

    if (options.id) params.append('id', options.id);
    if (options.ownerId) params.append('owner_id', options.ownerId);
    if (options.limit) params.append('limit', String(options.limit));
    if (options.offset) params.append('offset', String(options.offset));

    const url = `${this.dataUrl}/read/${table}?${params.toString()}`;
    const res = await fetch(url, {
      method: 'GET',
      headers: this.getHeaders(options.sessionToken),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`NCB Read failed (${res.status}): ${err}`);
    }

    return await res.json();
  }

  /**
   * Create a new record in a table
   */
  async create(table, data, options = {}) {
    if (!this.instance) throw new Error('NCB_INSTANCE is not configured in .env.');

    const params = new URLSearchParams({
      instance: this.instance,
    });
    if (options.ownerId) params.append('owner_id', options.ownerId);

    const url = `${this.dataUrl}/create/${table}?${params.toString()}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: this.getHeaders(options.sessionToken),
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`NCB Create failed (${res.status}): ${err}`);
    }

    return await res.json();
  }

  /**
   * Update a record by ID
   */
  async update(table, id, data, options = {}) {
    if (!this.instance) throw new Error('NCB_INSTANCE is not configured in .env.');
    if (!id) throw new Error('Record ID is required for update.');

    const params = new URLSearchParams({
      instance: this.instance,
      id: String(id),
    });

    const url = `${this.dataUrl}/update/${table}?${params.toString()}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: this.getHeaders(options.sessionToken),
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`NCB Update failed (${res.status}): ${err}`);
    }

    return await res.json();
  }

  /**
   * Delete a record by ID
   */
  async delete(table, id, options = {}) {
    if (!this.instance) throw new Error('NCB_INSTANCE is not configured in .env.');
    if (!id) throw new Error('Record ID is required for delete.');

    const params = new URLSearchParams({
      instance: this.instance,
      id: String(id),
    });

    const url = `${this.dataUrl}/delete/${table}?${params.toString()}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: this.getHeaders(options.sessionToken),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`NCB Delete failed (${res.status}): ${err}`);
    }

    return await res.json();
  }

  /**
   * Verify connectivity and instance status
   */
  async testConnection() {
    if (!this.instance) {
      return { ok: false, message: 'NCB_INSTANCE is empty.' };
    }

    try {
      // Test fetching auth-providers or read
      const testUrl = `${this.authUrl}/providers?instance=${this.instance}`;
      const res = await fetch(testUrl, {
        headers: this.getHeaders(),
      });
      return {
        ok: res.ok,
        status: res.status,
        instance: this.instance,
      };
    } catch (err) {
      return { ok: false, error: err.message, instance: this.instance };
    }
  }
}

export const ncb = new NoCodeBackendService();
export default ncb;
