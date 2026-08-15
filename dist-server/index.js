import { buildApp } from "./app.js";
const port = Number(process.env.PORT ?? 3000);
const app = await buildApp({
    apiKey: process.env.OPENROUTESERVICE_API_KEY ?? "",
    fetch,
    logger: { level: process.env.LOG_LEVEL ?? "info" }
});
for (const signal of ["SIGTERM", "SIGINT"]) {
    process.once(signal, () => {
        app.close().then(() => process.exit(0), () => process.exit(1));
    });
}
try {
    await app.listen({ host: "0.0.0.0", port });
}
catch (cause) {
    app.log.error(cause);
    process.exit(1);
}
