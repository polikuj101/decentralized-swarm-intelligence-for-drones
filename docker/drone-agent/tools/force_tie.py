import pathlib

p = pathlib.Path("/opt/swarm_agent/swarm/coordinator.py")
src = p.read_text()
target = "    def bid_for(self, j, now):"
replacement = (
    "    def bid_for(self, j, now):\n"
    "        import os\n"
    "        if os.environ.get('FORCE_TIE_TASK') and j == int(os.environ['FORCE_TIE_TASK']):\n"
    "            return 2700\n"
)
if target in src and "FORCE_TIE_TASK" not in src:
    p.write_text(src.replace(target, replacement, 1))
    print("force_tie: patched coordinator.py")
else:
    print("force_tie: already patched or target not found")
