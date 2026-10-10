// Tells Discord which slash commands the bot has. Run after any change to src/commands.js:
//   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... node register.mjs
// The deploy workflow runs it for you.
import { COMMANDS } from "./src/commands.js";

const { DISCORD_APP_ID: app, DISCORD_BOT_TOKEN: token } = process.env;
if (!app || !token) {
  console.error("set DISCORD_APP_ID and DISCORD_BOT_TOKEN");
  process.exit(2);
}
const res = await fetch(`https://discord.com/api/v10/applications/${app}/commands`, {
  method: "PUT",
  headers: { Authorization: `Bot ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify(COMMANDS),
});
if (!res.ok) {
  console.error(`Discord refused the commands: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
  process.exit(1);
}
console.log(`registered: ${COMMANDS.map((c) => "/" + c.name).join(" ")}`);
