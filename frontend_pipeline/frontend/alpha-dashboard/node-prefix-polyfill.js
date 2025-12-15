/**
 * Allow packages that import Node.js built-ins via the `node:` protocol
 * (e.g. `node:path`) to keep working on environments running Node < 14.
 */
const Module = require('module');
const nativeResolveFilename = Module._resolveFilename;

Module._resolveFilename = function patchedResolveFilename(request, parent, isMain, options) {
  if (typeof request === 'string' && request.startsWith('node:')) {
    request = request.slice(5);
  }

  return nativeResolveFilename.call(this, request, parent, isMain, options);
};
