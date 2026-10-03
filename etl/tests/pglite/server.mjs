// Local PGlite server for the ETL test suite (task h2-03b).
//
// PGlite is a WASM build of Postgres. It cannot be reached through libpq, so this
// script puts `@electric-sql/pglite-socket` in front of it and speaks the Postgres
// wire protocol on 127.0.0.1, which is what `postgresql+psycopg://` in Python needs.
//
// It is IN MEMORY ONLY: nothing is written to disk, and no credential, host or
// connection string is accepted from the environment. The parent pytest fixture
// passes the port back in through `ready <port>` on stdout, then applies
// `web/drizzle-v2/*.sql` over that same socket.
//
// Usage: node server.mjs
// Contract: prints `ready <port>` on stdout once the socket is listening.
//           exits 0 on SIGINT/SIGTERM, non-zero (1) on a startup failure.

import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { PGLiteSocketServer } from "@electric-sql/pglite-socket";

const HOST = "127.0.0.1";

// PGlite runs one transaction at a time. The parent fixture therefore shares one
// engine with short-lived connections, and anything that genuinely needs two
// connections at once is marked `pg_concurrent` and pointed at a real Postgres.
async function main() {
  const db = new PGlite({ extensions: { vector } });
  await db.waitReady;

  // port 0 lets the OS hand out a free port; `getServerConn()` reports the one
  // that was actually bound, which is what we print back to pytest.
  const server = new PGLiteSocketServer({ db, host: HOST, port: 0, maxConnections: 1 });
  await server.start();

  const port = Number(server.getServerConn().split(":").pop());
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error(`could not determine the bound port (got ${server.getServerConn()})`);
  }
  process.stdout.write(`ready ${port}\n`);

  let closing = false;
  const shutdown = async (signal) => {
    if (closing) return;
    closing = true;
    try {
      await server.stop();
      await db.close();
    } catch (err) {
      process.stderr.write(`shutdown after ${signal} failed: ${err?.stack ?? err}\n`);
      process.exit(1);
    }
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err) => {
  process.stderr.write(`pglite server failed to start: ${err?.stack ?? err}\n`);
  process.exit(1);
});