import os
import tempfile

# Importing desoto.app builds the default app; keep its data out of the repo.
os.environ.setdefault("DESOTO_DATA", tempfile.mkdtemp(prefix="desoto-test-"))
