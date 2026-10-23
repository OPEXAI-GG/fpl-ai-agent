"""C03 diagnostic ONLY (not a recommendation): the candidate plan with the implicit ownership
lock off (TEMPLATE_KEEP above 100%), on its own copy of the identical refreshed data."""
import sys

from fpl_agent import cli
from fpl_agent.solver import decide

decide.TEMPLATE_KEEP = 101.0  # nobody is "kept for ownership"
sys.exit(cli.main(["plan", "--db", sys.argv[1], "--entry", "8700026", "--out", sys.argv[2]]))
