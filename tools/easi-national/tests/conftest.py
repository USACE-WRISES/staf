import os

# owner decision D20: the builder suite scores with the bundled EASI method and never
# reaches the library release at import
os.environ.setdefault("EASI_ADOPTED_METHOD", "0")
