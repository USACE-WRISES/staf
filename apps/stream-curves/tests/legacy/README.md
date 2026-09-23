# Frozen readers of released clients

Byte-for-byte copies of modules as StreamCurves Desktop 1.0.0 shipped them (tag
`streamcurves-v1.0.0`, identical at ea32716). Tests load them to prove that files and feeds this
branch writes are read, or refused cleanly, by clients already installed:

- `project_file_1_0_0.py`: must refuse a format-2 project with "Update the app".
- `gallery_1_0_0.py`: must read `library.json` (schema 1) and never list an EASI method.

Never edit these files; replace them only with the module of another released version.
