// The slash commands the bot offers. register.mjs sends this list to Discord; worker.js answers it.
// integration_types 0 and 1: install it in a server, or on your own account so it works in DMs.
// contexts 0, 1 and 2: server channels, a DM with the bot, and other DMs / group DMs.
const STRING = 3;
const BOOLEAN = 5;
const everywhere = { integration_types: [0, 1], contexts: [0, 1, 2] };

export const COMMANDS = [
  {
    name: "ask",
    description: "Ask about your team and this week's plan, in plain words",
    options: [{ type: STRING, name: "question", description: "Your question", required: true }],
    ...everywhere,
  },
  {
    name: "latest",
    description: "Show the latest plan report",
    ...everywhere,
  },
  {
    name: "plan",
    description: "Plan now as a dry run: a report only, your team is not touched",
    options: [
      {
        type: BOOLEAN,
        name: "force",
        description: "Plan even if the deadline is more than 26 hours away",
        required: false,
      },
      {
        type: BOOLEAN,
        name: "real",
        description: "A real run instead: in live mode it makes the moves on your team",
        required: false,
      },
    ],
    ...everywhere,
  },
  {
    name: "explain",
    description: "Why players project as they do this gameweek",
    options: [
      {
        type: STRING,
        name: "players",
        description: "Names, comma separated (e.g. Salah, Haaland)",
        required: true,
      },
    ],
    ...everywhere,
  },
  {
    name: "status",
    description: "Dry or live, and how the last runs went",
    ...everywhere,
  },
];
