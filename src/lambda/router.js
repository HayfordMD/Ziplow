/**
 * Lightweight HTTP Router for Lambda & Local Development
 */
export class Router {
  constructor() {
    this.routes = [];
  }

  add(method, pathPattern, handler) {
    // Convert path pattern like '/api/data/:table' to regex
    const paramNames = [];
    const regexPath = pathPattern.replace(/:([a-zA-Z0-9_]+)/g, (_, paramName) => {
      paramNames.push(paramName);
      return '([^/]+)';
    });

    const regex = new RegExp(`^${regexPath}$`);
    this.routes.push({
      method: method.toUpperCase(),
      pattern: pathPattern,
      regex,
      paramNames,
      handler,
    });
  }

  get(path, handler) { this.add('GET', path, handler); }
  post(path, handler) { this.add('POST', path, handler); }
  put(path, handler) { this.add('PUT', path, handler); }
  delete(path, handler) { this.add('DELETE', path, handler); }

  async handle(req) {
    const { method, path } = req;

    for (const route of this.routes) {
      if (route.method !== method.toUpperCase() && route.method !== 'ALL') {
        continue;
      }

      const match = path.match(route.regex);
      if (match) {
        const params = {};
        route.paramNames.forEach((name, idx) => {
          params[name] = match[idx + 1];
        });
        req.params = params;
        return await route.handler(req);
      }
    }

    return {
      statusCode: 404,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: `Not Found: ${method} ${path}` }),
    };
  }
}

export default Router;
