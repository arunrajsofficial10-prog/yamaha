// Thin API client. Server errors arrive as {error: {code, message}}; message is user-safe.

export class ApiError extends Error {
  constructor(message, code, status) { super(message); this.code = code; this.status = status; }
}

// Anonymous id for this browser. On a public server it keeps each visitor's songs separate.
const CLIENT_ID = (() => {
  const fresh = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  try {
    let id = localStorage.getItem("lak.clientId");
    if (!id) { id = fresh(); localStorage.setItem("lak.clientId", id); }
    return id;
  } catch {
    return fresh(); // storage blocked: songs stay visible for this page session only
  }
})();

async function request(method, url, body) {
  let res;
  const headers = { "X-Client-Id": CLIENT_ID };
  if (body && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the server. Check your internet connection and try again.", "network", 0);
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const e = data?.error;
    throw new ApiError(e?.message || `Request failed (${res.status}).`, e?.code || "http", res.status);
  }
  return data;
}

export const styleApi = {
  keyboards: () => request("GET", "/api/keyboards"),
  report: (id, kb, q, part = 1) =>
    request("GET", `/api/projects/${id}/style?keyboard=${encodeURIComponent(kb)}&quantize=${q}&part=${part}`),
  styleUrl: (id, kb, q, part = 1) => `/api/projects/${id}/style.sty?keyboard=${encodeURIComponent(kb)}&quantize=${q}&part=${part}`,
  zipUrl: (id, kb, q) => `/api/projects/${id}/styles.zip?keyboard=${encodeURIComponent(kb)}&quantize=${q}`,
  midiUrl: (id) => `/api/projects/${id}/drums.mid`,
  saveVariations: (id, layout, styles) => request("PUT", `/api/projects/${id}/variations`, { layout, styles }),
};

export const api = {
  health: () => request("GET", "/api/health"),
  list: () => request("GET", "/api/projects"),
  get: (id) => request("GET", `/api/projects/${id}`),
  remove: (id) => request("DELETE", `/api/projects/${id}`),
  analyze: (id) => request("POST", `/api/projects/${id}/analyze`),
  job: (jobId) => request("GET", `/api/jobs/${jobId}`),
  correct: (id, patch) => request("PATCH", `/api/projects/${id}/corrections`, patch),
  audioUrl: (id) => `/api/projects/${id}/audio`,
  analysisUrl: (id) => `/api/projects/${id}/analysis.json`,
  exportUrl: (id) => `/api/projects/${id}/export.lakstyle`,

  // XHR for upload progress
  upload(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData();
      fd.append("file", file);
      xhr.open("POST", "/api/projects");
      xhr.setRequestHeader("X-Client-Id", CLIENT_ID);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError(data?.error?.message || `Upload failed (${xhr.status}).`, data?.error?.code, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError("Upload failed. Check your connection to the server.", "network", 0));
      xhr.send(fd);
    });
  },
};
