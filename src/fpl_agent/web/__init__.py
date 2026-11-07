"""Read-only website service around the engine.

Layers (one-way dependencies):

* `entry_ref`: turn a visitor's input (an entry ID or an FPL team URL) into a numeric ID;
* `store`: SQLite application state (sessions, jobs, provider cache, rate limits, results);
* `provider`: the only way the service reads FPL, through a fixed set of public endpoints,
  with caching, retries, stale fallback and an outbound budget;
* `snapshots`: immutable read-only copies of the football database for the service to read;
* `forecasts`: the shared forecast, built once per data revision and model configuration;
* `planning`: one manager's recommendation from the shared forecast and public squad state;
* `views`: dashboard, fixtures, players and model-performance payloads;
* `worker`: the bounded background worker that runs forecasts and plans as persistent jobs;
* `api`: the FastAPI application.

Nothing here signs in to FPL, changes a team or sends a notification.
"""
