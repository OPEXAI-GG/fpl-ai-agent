"""Independent synthetic dry integration; all network and auth are forbidden."""
import json
import os
from pathlib import Path
import socket
import sys

OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(OUT / 'repo/src'), str(OUT / 'repo')]
os.environ.update(AGENT_MODE='dry', AGENT_ENABLED='true', NOTIFY_WEBHOOK='',
                  SNAPSHOT_DIR=str(OUT / 'smoke-snapshots'))
for name in ('ODDS_API_KEY', 'ANTHROPIC_API_KEY'):
    os.environ.pop(name, None)

def forbidden(*args, **kwargs):
    raise AssertionError('Network/auth/notification forbidden in review smoke')

socket.socket.connect = forbidden
socket.create_connection = forbidden
from fpl_agent.config import Settings
from fpl_agent.agent import run_weekly
from fpl_agent import notify
from tests.test_agent import env, fake, opts

settings = Settings.from_env()
assert settings.enabled and not settings.is_live and settings.mode == 'dry'
notify.send = forbidden
world = env.__wrapped__()
try:
    result = run_weekly(world[0].db, settings,
                        opts(out_dir=OUT / 'smoke-output', notify=False),
                        fpl=fake(world), exchange=forbidden)
    assert result.status == 'ok', result.notes
    assert result.outcome.mode == 'dry' and result.outcome.ok
    assert all(step.startswith('would ') for step in result.outcome.done)
    assert world[0].db.con.execute('SELECT DISTINCT mode FROM decisions').fetchall() == [('dry',)]
    evidence = dict(status=result.status, mode=result.outcome.mode,
                    is_live=settings.is_live, enabled=settings.enabled,
                    recorded_decisions='dry', database='synthetic in-memory',
                    network='blocked', notifications=False, pass_=True)
    (OUT / 'dry-smoke.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence))
finally:
    world[0].db.close()
