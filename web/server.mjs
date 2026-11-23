// Zero-dependency Node.js local web server & bridge for D:\G12_Capstone\web.
// Fallback if Python is not installed. Run with: node server.mjs

import http from "node:http";
import https from "node:https";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.PORT || 8080);
const WEB_DIR = path.dirname(fileURLToPath(import.meta.url));

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon"
};

// Cached latest JPEG frame per board IP
const frameCache = new Map();

function isAllowedLocalHost(hostname) {
  if (!hostname) return false;
  const h = hostname.trim().toLowerCase();
  if (h === "localhost" || h === "127.0.0.1" || h === "::1" || h.endsWith(".local")) return true;
  if (/^10\./.test(h) || /^192\.168\./.test(h)) return true;
  if (/^172\.(1[6-9]|2\d|3[0-1])\./.test(h)) return true;
  if (/^169\.254\./.test(h)) return true;
  return false;
}

function sendJson(res, status, obj) {
  const body = Buffer.from(JSON.stringify(obj), "utf-8");
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": body.length,
    "Cache-Control": "no-store"
  });
  res.end(body);
}

function proxyRequest(req, res, targetStr) {
  let targetUrl;
  try {
    targetUrl = new URL(targetStr);
  } catch {
    return sendJson(res, 400, { error: "Invalid target URL" });
  }
  if (!["http:", "https:"].includes(targetUrl.protocol) || !isAllowedLocalHost(targetUrl.hostname)) {
    return sendJson(res, 400, { error: "Proxy only allows local/LAN hosts." });
  }

  const client = targetUrl.protocol === "https:" ? https : http;
  const headers = {};
  if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];

  const upReq = client.request(
    {
      hostname: targetUrl.hostname,
      port: targetUrl.port || (targetUrl.protocol === "https:" ? 443 : 80),
      path: targetUrl.pathname + targetUrl.search,
      method: req.method,
      headers
    },
    (upRes) => {
      res.writeHead(upRes.statusCode || 200, {
        "Content-Type": upRes.headers["content-type"] || "application/octet-stream",
        "Cache-Control": "no-store"
      });
      upRes.pipe(res);
    }
  );

  upReq.on("error", (err) => {
    if (!res.headersSent) sendJson(res, 502, { error: `Cannot reach ${targetStr}: ${err.message}` });
  });

  if (req.method === "POST") {
    req.pipe(upReq);
  } else {
    upReq.end();
  }
}

function proxyCameraStream(req, res, ip) {
  if (!isAllowedLocalHost(ip)) {
    return sendJson(res, 400, { error: "Only local/LAN camera IPs allowed." });
  }

  const upReq = http.get({ hostname: ip, port: 80, path: "/stream" }, (upRes) => {
    res.writeHead(upRes.statusCode || 200, {
      "Content-Type":
        upRes.headers["content-type"] ||
        "multipart/x-mixed-replace;boundary=123456789000000000000987654321",
      "Cache-Control": "no-store"
    });

    let scanBuf = Buffer.alloc(0);
    const SOI = Buffer.from([0xff, 0xd8]);
    const EOI = Buffer.from([0xff, 0xd9]);

    upRes.on("data", (chunk) => {
      res.write(chunk);
      scanBuf = Buffer.concat([scanBuf, chunk]);
      while (true) {
        const start = scanBuf.indexOf(SOI);
        if (start === -1) {
          if (scanBuf.length > 2) scanBuf = scanBuf.subarray(scanBuf.length - 2);
          break;
        }
        if (start > 0) scanBuf = scanBuf.subarray(start);
        const end = scanBuf.indexOf(EOI, 2);
        if (end === -1) {
          if (scanBuf.length > 2_000_000) scanBuf = Buffer.alloc(0);
          break;
        }
        const frame = Buffer.from(scanBuf.subarray(0, end + 2));
        frameCache.set(ip, { data: frame, ts: Date.now() });
        scanBuf = scanBuf.subarray(end + 2);
      }
    });

    upRes.on("end", () => res.end());
    req.on("close", () => upReq.destroy());
  });

  upReq.on("error", (err) => {
    if (!res.headersSent) sendJson(res, 502, { error: `Camera stream error: ${err.message}` });
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

  if (url.pathname === "/api/health") {
    return sendJson(res, 200, { ok: true, runtime: `node ${process.version}` });
  }
  if (url.pathname === "/api/proxy") {
    return proxyRequest(req, res, url.searchParams.get("target") || "");
  }
  if (url.pathname === "/api/camera/status") {
    const ip = url.searchParams.get("ip") || "";
    return proxyRequest(req, res, `http://${ip}/status`);
  }
  if (url.pathname === "/api/camera/stream") {
    return proxyCameraStream(req, res, url.searchParams.get("ip") || "");
  }
  if (url.pathname === "/api/camera/capture") {
    const ip = url.searchParams.get("ip") || "";
    const cached = frameCache.get(ip);
    if (cached && Date.now() - cached.ts < 5000) {
      res.writeHead(200, {
        "Content-Type": "image/jpeg",
        "Content-Length": cached.data.length,
        "Cache-Control": "no-store"
      });
      return res.end(cached.data);
    }
    return proxyRequest(req, res, `http://${ip}/capture`);
  }

  // Static files
  let relPath = decodeURIComponent(url.pathname);
  if (relPath === "/") relPath = "/index.html";
  const filePath = path.normalize(path.join(WEB_DIR, relPath));
  if (!filePath.startsWith(WEB_DIR)) {
    return sendJson(res, 403, { error: "Forbidden" });
  }
  fs.readFile(filePath, (err, content) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      return res.end("Not found");
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(content);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Student Assistant Website running at: http://127.0.0.1:${PORT}`);
  console.log("Press Ctrl+C to stop.");
});
