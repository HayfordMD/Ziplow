import config from '../config.js';

/**
 * MakersHost Media Storage Client
 * 
 * Uploads assets directly to the MakersHost cluster (storage.bannkd.com).
 * Based on production implementations in Bannkd, Whodatpokemon, and VeMe3.
 */
export class MakersHostService {
  constructor(customConfig = {}) {
    this.uploadUrl = customConfig.uploadUrl || config.storage.uploadUrl;
    this.apiKey = customConfig.apiKey || config.storage.apiKey;
    this.defaultFolder = customConfig.defaultFolder || config.storage.defaultFolder;
    this.maxSizeBytes = 10 * 1024 * 1024; // 10 MB limit
  }

  /**
   * Upload a Buffer, Blob, or File to MakersHost
   * 
   * @param {Buffer|Blob|Uint8Array} fileData - The binary file payload
   * @param {Object} options
   * @param {string} options.filename - Filename with extension (e.g. 'image.png')
   * @param {string} [options.folder] - Target folder (default: config.storage.defaultFolder)
   * @param {string} [options.mimeType] - MIME type (e.g. 'image/png')
   * @param {string} [options.name] - Custom asset name
   */
  async upload(fileData, options = {}) {
    if (!this.apiKey) {
      throw new Error(
        'MakersHost API Key missing. Set PUBLIC_STORAGE_API_KEY in your .env file.'
      );
    }

    const {
      filename = `upload_${Date.now()}.bin`,
      folder = this.defaultFolder,
      mimeType = 'application/octet-stream',
      name = '',
    } = options;

    // Convert Buffer to Blob if running in Node.js
    let blob;
    if (typeof Blob !== 'undefined' && fileData instanceof Blob) {
      blob = fileData;
    } else {
      blob = new Blob([fileData], { type: mimeType });
    }

    if (blob.size > this.maxSizeBytes) {
      const sizeMb = (blob.size / (1024 * 1024)).toFixed(2);
      throw new Error(`File "${filename}" exceeds 10MB limit (${sizeMb}MB).`);
    }

    const cleanName = name ? name.replace(/[^a-zA-Z0-9_-]/g, '_') : `${Date.now()}`;
    const cleanFilename = filename.replace(/[^a-zA-Z0-9_.-]/g, '_');

    // Build form data
    const formData = new FormData();
    formData.append('file', blob, cleanFilename);
    formData.append('folder', folder);
    formData.append('category', folder);
    if (name) {
      formData.append('name', cleanName);
    }

    const sendRequest = async (url) => {
      return await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
        },
        body: formData,
      });
    };

    // Execute request with HTTPS -> HTTP fallback
    let response;
    try {
      response = await sendRequest(this.uploadUrl);
      if (!response.ok && this.uploadUrl.startsWith('https://')) {
        const httpFallback = this.uploadUrl.replace(/^https:/, 'http:');
        try {
          const retryRes = await sendRequest(httpFallback);
          if (retryRes.ok) response = retryRes;
        } catch {
          // fallback failed, keep initial response
        }
      }
    } catch (err) {
      if (this.uploadUrl.startsWith('https://')) {
        const httpFallback = this.uploadUrl.replace(/^https:/, 'http:');
        response = await sendRequest(httpFallback);
      } else {
        throw err;
      }
    }

    const resText = await response.text();
    let resJson = null;
    try {
      resJson = JSON.parse(resText);
    } catch {
      // not JSON
    }

    let finalUrl = '';
    if (resJson) {
      finalUrl = resJson.url || resJson.file_url || resJson.link || resJson.location || resJson.data?.url || '';
    } else if (resText.trim().startsWith('http')) {
      finalUrl = resText.trim();
    }

    if (!response.ok || !finalUrl) {
      const errMsg = resJson?.error || resJson?.message || resText.replace(/<[^>]*>/g, '').trim().substring(0, 150) || `HTTP ${response.status}`;
      throw new Error(`MakersHost upload failed: ${errMsg}`);
    }

    // Resolve relative URL
    if (!finalUrl.startsWith('http://') && !finalUrl.startsWith('https://')) {
      const origin = new URL(this.uploadUrl).origin;
      finalUrl = `${origin}/${finalUrl.replace(/^\//, '')}`;
    }

    return {
      success: true,
      url: finalUrl,
      filename: resJson?.filename || cleanFilename,
      folder,
      size: blob.size,
      mimeType,
    };
  }

  /**
   * Upload from a local filesystem path
   */
  async uploadFile(filePath, options = {}) {
    const fs = await import('node:fs/promises');
    const path = await import('node:path');

    const buffer = await fs.readFile(filePath);
    const filename = options.filename || path.basename(filePath);
    return await this.upload(buffer, { ...options, filename });
  }

  /**
   * Health ping to MakersHost upload endpoint
   */
  async ping() {
    try {
      const res = await fetch(this.uploadUrl, { method: 'OPTIONS' });
      return { ok: true, status: res.status };
    } catch (err) {
      // Try GET/HEAD if OPTIONS fails
      try {
        const res = await fetch(this.uploadUrl);
        return { ok: true, status: res.status };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    }
  }
}

export const makersHost = new MakersHostService();
export default makersHost;
