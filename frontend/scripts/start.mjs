import { cpSync, existsSync } from "node:fs";
import { isIP } from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function options() {
  const settings = {
    port: process.env.PORT ?? "3000",
    hostname: process.env.HOSTNAME ?? "0.0.0.0",
  };
  const args = process.argv.slice(2);
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index];
    if (name === "--help" && args.length === 1) {
      console.log("Usage: npm start -- [--port 3000] [--hostname 0.0.0.0]");
      return;
    }
    if (!["--port", "--hostname"].includes(name) || !args[index + 1]) {
      throw new Error(
        "Only --port and --hostname, each followed by a value, are supported.",
      );
    }
    settings[name.slice(2)] = args[index + 1];
  }
  if (
    !/^\d+$/.test(settings.port) ||
    Number(settings.port) < 1 ||
    Number(settings.port) > 65535
  ) {
    throw new Error("Port must be an integer between 1 and 65535.");
  }
  const validHostname =
    settings.hostname.length <= 253 &&
    settings.hostname
      .split(".")
      .every((label) => /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/i.test(label));
  if (!isIP(settings.hostname) && !validHostname) {
    throw new Error("Hostname must be a valid IP address or DNS hostname.");
  }
  return settings;
}

async function main() {
  const settings = options();
  if (!settings) return;
  const standalone = path.join(root, ".next", "standalone");
  const server = path.join(standalone, "server.js");
  const staticAssets = path.join(root, ".next", "static");
  if (!existsSync(server) || !existsSync(staticAssets)) {
    throw new Error(
      "Production build is missing. Run npm run build before npm start.",
    );
  }
  cpSync(staticAssets, path.join(standalone, ".next", "static"), {
    recursive: true,
  });
  const publicAssets = path.join(root, "public");
  if (existsSync(publicAssets))
    cpSync(publicAssets, path.join(standalone, "public"), { recursive: true });
  process.env.NODE_ENV = "production";
  process.env.PORT = settings.port;
  process.env.HOSTNAME = settings.hostname;
  await import(pathToFileURL(server).href);
}

main().catch((error) => {
  console.error(`ResolveAI could not start: ${error.message}`);
  process.exitCode = 1;
});
