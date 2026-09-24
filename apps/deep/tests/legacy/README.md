# Frozen readers of DEEP releases

Byte-for-byte copies of `deep/remote_library.py` and `deep/library.py` at ea32716 (the DEEP that
the next deploy ships; the DEEP deployed on 2026-09-09 has no remote library and reads its baked
registry only). Tests load them to prove the library feed and catalog this branch writes never
reach them as a DEEP assessment when they are not one.

Never edit these files; replace them only with the modules of another deployed version.
