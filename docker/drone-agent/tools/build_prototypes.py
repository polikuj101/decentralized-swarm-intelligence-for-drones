import os

from swarm.embedder import Embedder, build_protos

d = os.environ.get("S1_EMBED_DIR", "/opt/models/embed")
e = Embedder(d, dim=int(os.environ.get("S1_EMBED_DIM", "128")))
build_protos(e, os.path.join(d, "protos.npz"))
print("protos built:", e.tag)
